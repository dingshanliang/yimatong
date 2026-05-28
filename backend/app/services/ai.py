"""AI 资料识别与文案生成服务（模拟实现）"""


def generate_copywriting(
    copy_type: str, product_name: str, keywords: list[str],
) -> dict:
    """生成文案（模拟）"""
    if copy_type == "brand_story":
        kw_str = "、".join(keywords) if keywords else "品质"
        content = f"{product_name}，{kw_str}。源自大自然的馈赠，每一份都承载着匠心与诚意。从产地到餐桌，我们用心守护每一个环节，只为让您品尝到最纯正的美味。"
        return {"content": content}
    elif copy_type == "selling_points":
        items = [f"{kw}：{product_name}的核心优势" for kw in keywords] if keywords else ["优质品质"]
        return {"content": {"items": items}}
    return {"content": ""}


def extract_product_fields(text: str) -> dict:
    """从文本提取产品字段（模拟规则匹配）"""
    fields = {}
    if "脐橙" in text:
        fields["product_name"] = "脐橙"
    elif "蜂蜜" in text:
        fields["product_name"] = "蜂蜜"
    elif "大米" in text:
        fields["product_name"] = "大米"
    else:
        fields["product_name"] = "未知产品"

    # 简单规则提取产地
    for prefix in ["产地", "来自", "源自"]:
        if prefix in text:
            idx = text.index(prefix)
            segment = text[idx + len(prefix):idx + len(prefix) + 10]
            for sep in ["，", "、", "。", "，", ","]:
                if sep in segment:
                    segment = segment[:segment.index(sep)]
                    break
            fields["origin"] = segment.strip()
            break

    if "origin" not in fields:
        fields["origin"] = "未知"

    # 提取重量
    import re
    weight_match = re.search(r"(\d+\.?\d*)\s*(kg|公斤|斤|g|克)", text)
    if weight_match:
        fields["weight"] = f"{weight_match.group(1)}{weight_match.group(2)}"

    # 提取保质期
    shelf_match = re.search(r"保质期[：:]?\s*(\d+)\s*(天|月|年)", text)
    if shelf_match:
        fields["shelf_life"] = f"{shelf_match.group(1)}{shelf_match.group(2)}"

    return {"fields": fields}


def suggest_page_structure(product_name: str, category: str) -> dict:
    """页面结构建议（模拟）"""
    base_modules = ["hero_banner", "product_info", "traceability"]

    if category in ["水果", "蔬菜"]:
        base_modules.extend(["origin_map", "seasonal_calendar"])
    elif category in ["粮食", "食用油"]:
        base_modules.extend(["nutrition_facts", "cooking_tips"])
    elif category in ["蜂蜜", "茶叶"]:
        base_modules.extend(["craftsmanship", "tasting_notes"])
    else:
        base_modules.append("reviews")

    return {"modules": base_modules}


def generate_campaign(
    product_name: str, goal: str, target_audience: str,
) -> dict:
    """生成活动方案（模拟）"""
    if goal == "promotion":
        name = f"{product_name}扫码有礼"
        description = f"面向{target_audience}，扫描{product_name}包装二维码即可参与抽奖活动，赢取精美礼品。"
        benefits = ["优惠券", "积分奖励", "限量周边"]
    elif goal == "retention":
        name = f"{product_name}复购回馈"
        description = f"针对{target_audience}，多次扫码累积积分可兑换{product_name}系列产品。"
        benefits = ["积分翻倍", "专属折扣", "新品试吃"]
    else:
        name = f"{product_name}品牌推广"
        description = f"为{target_audience}提供{product_name}的品牌体验活动。"
        benefits = ["品牌周边", "体验装", "会员权益"]

    return {
        "name": name,
        "description": description,
        "suggested_benefits": benefits,
    }
