"""CRM 同步冲突处理模块。

字段级 Source of Truth 策略：
- 手机号、昵称：一码通优先（消费者在一码通主动填写）
- 标签：双向合并（取并集）
- 会员等级：一码通优先（由积分体系决定）
- CRM 自定义字段：CRM 优先（写入 extra_data）
"""

from dataclasses import dataclass, field


@dataclass
class FieldConflict:
    field_name: str
    local_value: str | None
    remote_value: str | None
    resolved_value: str | None
    source: str  # "local" | "remote" | "merged"


@dataclass
class ConflictResult:
    updates: dict = field(default_factory=dict)
    conflicts: list[FieldConflict] = field(default_factory=list)
    tag_merge_details: dict | None = None


# 一码通为主的字段（本地值优先）
LOCAL_PRIORITY_FIELDS = {"phone_hash", "nickname", "member_level"}

# CRM 为主的自定义字段前缀（写入 extra_data）
CRM_CUSTOM_FIELD_PREFIX = "crm_"


def resolve_consumer_conflict(
    local_data: dict,
    remote_data: dict,
    local_updated_at: str | None = None,
    remote_updated_at: str | None = None,
) -> ConflictResult:
    """解决消费者数据的双向同步冲突。

    Args:
        local_data: 一码通 ConsumerProfile 的当前数据
        remote_data: CRM 侧拉取的数据
        local_updated_at: 本地最后更新时间（ISO 格式）
        remote_updated_at: CRM 侧最后更新时间（ISO 格式）

    Returns:
        ConflictResult with resolved values and conflict details
    """
    result = ConflictResult()
    updates = {}

    for key in set(list(local_data.keys()) + list(remote_data.keys())):
        if key == "tags":
            continue  # 标签单独处理

        local_val = local_data.get(key)
        remote_val = remote_data.get(key)

        if local_val == remote_val:
            continue

        if key in LOCAL_PRIORITY_FIELDS:
            resolved = local_val if local_val is not None else remote_val
            source = "local" if local_val is not None else "remote"
        elif key.startswith(CRM_CUSTOM_FIELD_PREFIX):
            resolved = remote_val
            source = "remote"
        else:
            # 默认：取最新的值
            resolved = remote_val if remote_val is not None else local_val
            source = "remote" if remote_val is not None else "local"

        if resolved is not None and resolved != local_val:
            updates[key] = resolved

        if local_val != remote_val and local_val is not None and remote_val is not None:
            result.conflicts.append(
                FieldConflict(
                    field_name=key,
                    local_value=str(local_val),
                    remote_value=str(remote_val),
                    resolved_value=str(resolved),
                    source=source,
                )
            )

    # 标签取并集
    local_tags = _parse_tags(local_data.get("tags", ""))
    remote_tags = _parse_tags(remote_data.get("tags", ""))
    merged_tags = local_tags | remote_tags
    merged_str = ",".join(sorted(merged_tags))

    if merged_str != (local_data.get("tags") or ""):
        updates["tags"] = merged_str

    if local_tags != remote_tags:
        result.tag_merge_details = {
            "local_tags": sorted(local_tags),
            "remote_tags": sorted(remote_tags),
            "merged_tags": sorted(merged_tags),
            "added_from_remote": sorted(remote_tags - local_tags),
            "added_from_local": sorted(local_tags - remote_tags),
        }

    result.updates = updates
    return result


def _parse_tags(tags_str: str | None) -> set[str]:
    """解析标签字符串为 Set"""
    if not tags_str:
        return set()
    return {t.strip() for t in tags_str.split(",") if t.strip()}
