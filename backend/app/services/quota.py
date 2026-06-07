class QuotaExceededError(Exception):
    pass


# Standard quota keys used across the platform
QUOTA_KEYS = {
    "max_codes",
    "max_campaigns",
    "max_products",
    "max_accounts",
    "max_active_campaigns",
    "max_codes_per_batch",
}


def check_quota(quota: dict | None, resource_type: str, amount: int) -> None:
    """Check if a single operation amount exceeds the quota limit.

    Example: check_quota(tenant.quota, "max_codes_per_batch", 1000)
    """
    if not quota:
        return
    limit = quota.get(resource_type)
    if limit is None:
        return
    if amount > limit:
        raise QuotaExceededError(
            f"Quota exceeded for {resource_type}: requested {amount}, limit {limit}"
        )


def check_quota_incremental(
    quota: dict | None,
    resource_type: str,
    current_usage: int,
    additional_amount: int,
) -> None:
    """Check if current usage + additional amount would exceed quota.

    Example: check_quota_incremental(tenant.quota, "max_campaigns", 5, 1)
    """
    if not quota:
        return
    limit = quota.get(resource_type)
    if limit is None:
        return
    total = current_usage + additional_amount
    if total > limit:
        raise QuotaExceededError(
            f"Quota exceeded for {resource_type}: "
            f"current {current_usage} + additional {additional_amount} = {total}, limit {limit}"
        )
