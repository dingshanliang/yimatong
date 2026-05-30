"""码解析服务"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.code import CodeBatch, CodeItem
from app.models.page import PageTemplate


async def resolve_public_code(
    db: AsyncSession,
    public_id: str,
) -> dict | None:
    """解析公开码，返回码信息+关联数据"""
    result = await db.execute(select(CodeItem).where(CodeItem.public_id == public_id))
    item = result.scalar_one_or_none()
    if not item:
        return None

    data = {
        "public_id": item.public_id,
        "status": item.status,
        "tenant_id": str(item.tenant_id),
        "code_batch_id": str(item.code_batch_id),
        "code_type": item.code_type,
        "pair_id": str(item.pair_id) if item.pair_id else None,
    }

    # 获取批次和产品信息
    batch_result = await db.execute(select(CodeBatch).where(CodeBatch.id == item.code_batch_id))
    batch = batch_result.scalar_one_or_none()
    if batch:
        data["product_id"] = str(batch.product_id)
        data["sku_id"] = str(batch.sku_id)

        # 查找产品关联的页面模板
        tmpl_result = await db.execute(
            select(PageTemplate)
            .where(
                PageTemplate.tenant_id == item.tenant_id,
                PageTemplate.product_id == batch.product_id,
                PageTemplate.status == "active",
            )
            .limit(1)
        )
        template = tmpl_result.scalar_one_or_none()
        if template:
            data["template_id"] = str(template.id)

    return data


# 外码引流页模板
OUTER_LANDING_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>产品引流页</title>
<style>
body {{ font-family: sans-serif; margin: 0; padding: 16px; background: #f8f9fa; }}
.card {{ background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
h2 {{ color: #1a1a1a; margin: 0 0 12px; }}
.hint {{ background: #e6f7ff; border: 1px solid #91d5ff; border-radius: 8px; padding: 12px; margin-top: 16px; }}
.hint p {{ margin: 0; color: #0050b3; }}
</style>
</head>
<body>
<div class="card">
<h2>产品信息</h2>
<p>码编号: {public_id}</p>
<div class="hint">
<p>请刮开包装内侧涂层，扫描内码验真领奖</p>
</div>
</div>
</body></html>"""

# 内码验真页模板
INNER_VERIFY_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>验真结果</title>
<style>
body {{ font-family: sans-serif; margin: 0; padding: 16px; background: #f8f9fa; }}
.card {{ background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.badge {{ display: inline-block; background: #52c41a; color: #fff;
  padding: 4px 12px; border-radius: 12px; font-size: 14px; }}
h2 {{ color: #1a1a1a; margin: 0 0 12px; }}
</style>
</head>
<body>
<div class="card">
<h2>验真结果 <span class="badge">正品保障</span></h2>
<p>码编号: {public_id}</p>
<p>该产品为正品，请放心使用</p>
</div>
</body></html>"""

# 通用降级/提示页面
NOT_FOUND_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>码不存在</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px 16px;">
<h2>该二维码无效</h2><p>请核实后重试</p>
</body></html>"""

REVOKED_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>码已作废</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px 16px;">
<h2>该二维码已作废</h2><p>如有疑问请联系客服</p>
</body></html>"""

NOT_ACTIVE_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>尚未启用</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px 16px;">
<h2>此码尚未启用</h2><p>请联系厂家激活</p>
</body></html>"""

RISK_FROZEN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>风险冻结</title>
<style>
body { font-family: sans-serif; text-align: center; padding: 40px 16px; background: #fff2f0; }
.warn { color: #cf1322; }
</style>
</head>
<body>
<h2 class="warn">该码已被风险冻结</h2>
<p>系统检测到异常行为，该码已被临时冻结</p>
<p>如有疑问请联系客服</p>
</body>
</html>"""

DEFAULT_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>产品信息</title></head>
<body style="font-family:sans-serif;padding:16px;">
<h2>产品信息</h2><p>请稍后访问获取详细信息</p>
</body></html>"""
