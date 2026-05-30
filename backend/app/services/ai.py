"""AI 资料识别与文案生成服务

使用 DeepSeek V4 通过 Tool Calls 实现结构化输出。
支持分级容错、API Key 池轮询、租户日限额。
"""

import json
import logging
import uuid
from datetime import date

import redis.asyncio as aioredis
from openai import APIConnectionError, APIStatusError, APITimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_generation import AiGeneration
from app.services.llm_pool import get_pool
from app.services.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    """AI 服务错误（用户可见）"""

    pass


class AIRateLimitError(AIServiceError):
    """超出日限额"""

    pass


class AIServiceUnavailableError(AIServiceError):
    """AI 服务不可用"""

    pass


async def _check_daily_limit(tenant_id: uuid.UUID, db: AsyncSession) -> None:
    """检查租户当日 AI 调用限额"""
    limit = settings.ai_daily_limit_per_tenant
    if limit <= 0:
        return

    today = date.today().isoformat()
    key = f"ai:daily:{tenant_id}:{today}"

    try:
        r = aioredis.from_url(settings.redis_url)
        try:
            count = await r.get(key)
            if count is not None and int(count) >= limit:
                raise AIRateLimitError(f"今日 AI 调用次数已达上限（{limit} 次），请明天再试")
        finally:
            await r.aclose()
    except Exception as e:
        if isinstance(e, AIRateLimitError):
            raise
        logger.warning("Redis 不可用，跳过日限额检查: %s", e)


async def _increment_daily_count(tenant_id: uuid.UUID) -> None:
    """增加租户当日 AI 调用计数"""
    limit = settings.ai_daily_limit_per_tenant
    if limit <= 0:
        return

    today = date.today().isoformat()
    key = f"ai:daily:{tenant_id}:{today}"

    try:
        r = aioredis.from_url(settings.redis_url)
        try:
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, 86400 * 2)  # 2 天过期
            await pipe.execute()
        finally:
            await r.aclose()
    except Exception as e:
        logger.warning("Redis 不可用，跳过计数: %s", e)


async def _call_llm(
    messages: list[dict],
    tools: list[dict],
    model: str | None = None,
) -> dict:
    """调用 LLM 并通过 Tool Call 返回结构化结果

    包含分级容错：限流等待重试、格式错误重试、网络故障快速失败。
    """
    pool = get_pool()
    model = model or settings.deepseek_model_default

    max_attempts = 2
    for attempt in range(max_attempts):
        client = pool.get_client()
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice={"type": "function", "function": {"name": tools[0]["function"]["name"]}},
                temperature=0.7,
                max_tokens=4096,
                extra_body={"thinking": {"type": "disabled"}},
            )

            # 提取 tool call 参数
            choice = response.choices[0]
            if choice.message.tool_calls:
                tool_call = choice.message.tool_calls[0]
                result = json.loads(tool_call.function.arguments)
                return result

            # 模型没有调用 tool，重试并强调
            if attempt == 0:
                messages.append(choice.message.model_dump())
                messages.append({
                    "role": "user",
                    "content": "请通过调用提供的函数来返回结果，不要直接回复文字。",
                })
                continue

            raise AIServiceError("AI 未返回结构化结果，请重试")

        except APIStatusError as e:
            if e.status_code == 429:
                pool.mark_rate_limited(cooldown_seconds=5.0)
                if attempt < max_attempts - 1:
                    logger.warning("API 限流，尝试使用其他 key 重试")
                    continue
                raise AIServiceUnavailableError("AI 服务繁忙，请稍后重试") from e
            logger.error("API 错误: status=%d body=%s", e.status_code, e.response.text[:200])
            raise AIServiceError(f"AI 服务错误 ({e.status_code})") from e

        except APIConnectionError as e:
            logger.error("API 连接失败: %s", e)
            raise AIServiceUnavailableError("AI 服务暂时不可用，请稍后重试") from e

        except APITimeoutError as e:
            logger.error("API 超时: %s", e)
            raise AIServiceUnavailableError("AI 服务响应超时，请稍后重试") from e

        except json.JSONDecodeError as e:
            logger.warning("Tool call 参数 JSON 解析失败 (attempt %d): %s", attempt, e)
            if attempt < max_attempts - 1:
                continue
            raise AIServiceError("AI 返回格式异常，请重试") from e

    raise AIServiceError("AI 调用失败，请重试")


async def _save_generation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    gen_type: str,
    input_snapshot: dict,
    output_data: dict,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    model_version: str | None = None,
) -> AiGeneration:
    """持久化 AI 生成记录"""
    record = AiGeneration(
        tenant_id=tenant_id,
        type=gen_type,
        target_type=target_type,
        target_id=target_id,
        input_snapshot=input_snapshot,
        output_data=output_data,
        status="draft",
        model_version=model_version or settings.deepseek_model_default,
    )
    db.add(record)
    await db.flush()
    return record


def _build_messages(system_prompt: str, user_content: str | list) -> list[dict]:
    """构建消息列表"""
    messages = [{"role": "system", "content": system_prompt}]
    if isinstance(user_content, str):
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": user_content})
    return messages


# ──────────────────────── 公开 API ────────────────────────


async def extract_product_fields(
    text: str,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> dict:
    """AI-01a: 从文本提取产品字段"""
    await _check_daily_limit(tenant_id, db)

    prompt = load_prompt("extract_text")
    messages = _build_messages(prompt["system"], f"请从以下文本中提取产品信息：\n\n{text}")
    tools = [{"type": "function", "function": prompt["tool"]}]

    result = await _call_llm(messages, tools)
    await _increment_daily_count(tenant_id)

    record = await _save_generation(
        db, tenant_id, "extract_text",
        input_snapshot={"text": text[:2000]},
        output_data=result,
    )
    await db.commit()

    return {"fields": result, "generation_id": str(record.id)}


async def extract_product_from_image(
    image_url: str,
    filename: str,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> dict:
    """AI-01b: 从图片识别产品信息"""
    await _check_daily_limit(tenant_id, db)

    prompt = load_prompt("extract_image")
    user_content = [
        {"type": "text", "text": "请识别这张图片中的产品信息："},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    messages = _build_messages(prompt["system"], user_content)
    tools = [{"type": "function", "function": prompt["tool"]}]

    result = await _call_llm(messages, tools)
    await _increment_daily_count(tenant_id)

    record = await _save_generation(
        db, tenant_id, "extract_image",
        input_snapshot={"image_url": image_url, "filename": filename},
        output_data=result,
    )
    await db.commit()

    return {"fields": result, "generation_id": str(record.id)}


async def generate_copywriting(
    copy_type: str,
    product_name: str,
    keywords: list[str],
    tenant_id: uuid.UUID,
    db: AsyncSession,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
) -> dict:
    """AI-02: 生成文案"""
    await _check_daily_limit(tenant_id, db)

    prompt = load_prompt("copywriting")
    kw_str = "、".join(keywords) if keywords else "品质"
    user_msg = (
        f"请为「{product_name}」生成{copy_type}。\n"
        f"关键词：{kw_str}\n"
        f"文案类型：{'品牌故事' if copy_type == 'brand_story' else '产品卖点'}"
    )
    messages = _build_messages(prompt["system"], user_msg)
    tools = [{"type": "function", "function": prompt["tool"]}]

    result = await _call_llm(messages, tools)
    await _increment_daily_count(tenant_id)

    record = await _save_generation(
        db, tenant_id, "copywriting",
        input_snapshot={"copy_type": copy_type, "product_name": product_name, "keywords": keywords},
        output_data=result,
        target_type=target_type,
        target_id=target_id,
    )
    await db.commit()

    return {"content": result.get("content", ""), "generation_id": str(record.id)}


async def suggest_page_structure(
    product_name: str,
    category: str,
    tenant_id: uuid.UUID,
    db: AsyncSession,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
) -> dict:
    """AI-03: 页面结构建议"""
    await _check_daily_limit(tenant_id, db)

    prompt = load_prompt("page_suggest")
    user_msg = (
        f"请为「{product_name}」（品类：{category}）推荐扫码页面的模块结构。\n"
        f"目标读者是扫描产品包装二维码的消费者。"
    )
    messages = _build_messages(prompt["system"], user_msg)
    tools = [{"type": "function", "function": prompt["tool"]}]

    result = await _call_llm(messages, tools)
    await _increment_daily_count(tenant_id)

    record = await _save_generation(
        db, tenant_id, "page_suggest",
        input_snapshot={"product_name": product_name, "category": category},
        output_data=result,
        target_type=target_type,
        target_id=target_id,
    )
    await db.commit()

    return {"suggestion": result, "generation_id": str(record.id)}


async def generate_page_copy(
    product_name: str,
    category: str,
    keywords: list[str],
    tenant_id: uuid.UUID,
    db: AsyncSession,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
) -> dict:
    """AI-02+03 综合: 生成页面文案和推荐模板"""
    await _check_daily_limit(tenant_id, db)

    prompt = load_prompt("page_copy")
    kw_str = "、".join(keywords) if keywords else "品质"
    user_msg = (
        f"请为「{product_name}」（品类：{category}）生成完整的页面文案方案。\n"
        f"关键词：{kw_str}\n"
        f"需要同时提供文案内容和推荐的页面模块结构。"
    )
    messages = _build_messages(prompt["system"], user_msg)
    tools = [{"type": "function", "function": prompt["tool"]}]

    result = await _call_llm(messages, tools)
    await _increment_daily_count(tenant_id)

    record = await _save_generation(
        db, tenant_id, "page_copy",
        input_snapshot={"product_name": product_name, "category": category, "keywords": keywords},
        output_data=result,
        target_type=target_type,
        target_id=target_id,
    )
    await db.commit()

    return {"result": result, "generation_id": str(record.id)}


async def generate_campaign(
    product_name: str,
    goal: str,
    target_audience: str,
    tenant_id: uuid.UUID,
    db: AsyncSession,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
) -> dict:
    """AI-04: 生成活动方案"""
    await _check_daily_limit(tenant_id, db)

    prompt = load_prompt("campaign")
    goal_labels = {
        "promotion": "拉新推广",
        "retention": "复购留存",
        "brand_awareness": "品牌认知",
        "festival": "节日活动",
    }
    user_msg = (
        f"请为「{product_name}」设计一个扫码营销活动方案。\n"
        f"活动目标：{goal_labels.get(goal, goal)}\n"
        f"目标受众：{target_audience}"
    )
    messages = _build_messages(prompt["system"], user_msg)
    tools = [{"type": "function", "function": prompt["tool"]}]

    result = await _call_llm(messages, tools)
    await _increment_daily_count(tenant_id)

    record = await _save_generation(
        db, tenant_id, "campaign",
        input_snapshot={"product_name": product_name, "goal": goal, "target_audience": target_audience},
        output_data=result,
        target_type=target_type,
        target_id=target_id,
    )
    await db.commit()

    return {"campaign": result, "generation_id": str(record.id)}
