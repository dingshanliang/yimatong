"""行业默认品类映射表。

租户创建时根据 industry 字段取对应品类列表填充 tenant.categories。
"""

INDUSTRY_DEFAULT_CATEGORIES: dict[str, list[str]] = {
    "茶叶": ["绿茶", "红茶", "乌龙茶", "白茶", "黄茶", "黑茶", "花茶", "普洱茶"],
    "水果": ["苹果", "橙子", "草莓", "葡萄", "猕猴桃", "桃子", "梨", "柑橘"],
    "大米": ["籼米", "粳米", "糯米", "糙米", "有机大米", "富硒大米"],
    "农产品": ["蔬菜", "水果", "粮食", "食用油", "蜂蜜", "坚果", "菌菇"],
    "食品": ["大米", "面粉", "食用油", "茶叶", "零食", "饮料", "酒类", "乳制品", "保健品"],
    "酒类": ["白酒", "红酒", "啤酒", "黄酒", "果酒", "米酒"],
    "乳制品": ["牛奶", "酸奶", "奶酪", "奶粉", "黄油"],
    "食用油": ["花生油", "菜籽油", "大豆油", "橄榄油", "芝麻油", "葵花籽油"],
    "饮料": ["碳酸饮料", "果汁", "茶饮料", "功能饮料", "矿泉水"],
    "零食": ["饼干", "薯片", "坚果", "糖果", "巧克力", "肉干"],
}

DEFAULT_CATEGORIES: list[str] = ["其他"]


def get_default_categories(industry: str | None) -> list[str]:
    """根据行业返回默认品类列表。"""
    if industry and industry in INDUSTRY_DEFAULT_CATEGORIES:
        return INDUSTRY_DEFAULT_CATEGORIES[industry][:]
    return DEFAULT_CATEGORIES[:]
