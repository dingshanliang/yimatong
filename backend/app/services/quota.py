class QuotaExceededError(Exception):
    pass


def check_quota(quota: dict, resource_type: str, amount: int) -> None:
    limit = quota.get(resource_type, 0)
    if amount > limit:
        raise QuotaExceededError(f"Quota exceeded for {resource_type}: requested {amount}, limit {limit}")
