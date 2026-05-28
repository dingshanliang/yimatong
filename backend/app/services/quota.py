class QuotaExceededError(Exception):
    pass


def check_quota(quota: dict | None, resource_type: str, amount: int) -> None:
    if not quota:
        return
    limit = quota.get(resource_type)
    if limit is None:
        return
    if amount > limit:
        raise QuotaExceededError(f"Quota exceeded for {resource_type}: requested {amount}, limit {limit}")
