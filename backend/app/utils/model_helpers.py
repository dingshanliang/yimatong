"""通用 ORM 模型辅助函数。"""


def apply_allowed_updates(obj, kwargs: dict, allowed_fields: set[str]) -> None:
    """将 kwargs 中属于 allowed_fields 的键值安全地应用到对象属性上。

    跳过值为 None 的条目（保留字段现有值）。
    """
    for key, value in kwargs.items():
        if key in allowed_fields and value is not None:
            setattr(obj, key, value)
