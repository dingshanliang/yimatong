"""bind consumer consent authority

Revision ID: u6e2e3f4a5b6
Revises: u6e1d2e3f4a5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6e2e3f4a5b6"
down_revision: str | None = "u6e1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICIES = (
    (
        "privacy_policy",
        "privacy",
        "2026-07-27-v1",
        "隐私政策与个人信息处理告知",
        "我们仅在提供扫码溯源、会员与权益服务所必需的范围内处理个人信息。您可随时撤回同意；撤回不影响此前处理的合法性。",
    ),
    (
        "lead_capture",
        "marketing",
        "2026-07-27-v1",
        "联系信息收集与营销沟通同意",
        "经您主动同意后，我们会保存您提交的联系方式、称呼、地区和意向，用于品牌咨询与后续沟通。您可随时撤回，撤回后联系信息将被抑制并删除可恢复的线索字段。",
    ),
    (
        "wechat_cash_payout",
        "data_share",
        "2026-08-11-v1",
        "微信现金权益发放授权",
        "经您主动同意后，我们仅为发放已领取的现金权益使用受保护的微信身份标识，并按照交易、审计和合规要求保留必要事实。",
    ),
)


def _add_not_valid(name: str, table: str, clause: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} {clause} NOT VALID")
    op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for purpose, consent_type, version, title, content in _POLICIES:
        bind.execute(
            sa.text(
                "INSERT INTO public.consumer_consent_policies "
                "(id,tenant_id,purpose,consent_type,policy_version,policy_digest,policy_title,policy_content,effective_at) "
                "SELECT gen_random_uuid(),tenant.id,:purpose,:consent_type,:version,"
                "encode(digest(convert_to(:content,'UTF8'),'sha256'),'hex'),:title,:content,now() FROM public.tenants AS tenant "
                "ON CONFLICT (tenant_id,purpose,policy_version) DO NOTHING"
            ),
            {"purpose": purpose, "consent_type": consent_type, "version": version, "title": title, "content": content},
        )
        bind.execute(
            sa.text(
                "INSERT INTO public.consumer_consent_policy_current (tenant_id,purpose,policy_id) "
                "SELECT policy.tenant_id,policy.purpose,policy.id FROM public.consumer_consent_policies AS policy "
                "WHERE policy.purpose=:purpose AND policy.policy_version=:version "
                "ON CONFLICT (tenant_id,purpose) DO NOTHING"
            ),
            {"purpose": purpose, "version": version},
        )

    op.execute(
        "ALTER TABLE public.consumer_profiles ADD CONSTRAINT uq_consumer_profiles_tenant_id "
        "UNIQUE USING INDEX uq_consumer_profiles_tenant_id"
    )
    op.execute(
        "ALTER TABLE public.consent_records ADD CONSTRAINT uq_consent_records_tenant_id "
        "UNIQUE USING INDEX uq_consent_records_tenant_id"
    )
    _add_not_valid(
        "fk_consent_records_tenant",
        "consent_records",
        "FOREIGN KEY (tenant_id) REFERENCES public.tenants(id)",
    )
    _add_not_valid(
        "fk_consent_records_tenant_consumer",
        "consent_records",
        "FOREIGN KEY (tenant_id,consumer_id) REFERENCES public.consumer_profiles(tenant_id,id)",
    )
    _add_not_valid(
        "fk_consent_records_tenant_policy",
        "consent_records",
        "FOREIGN KEY (tenant_id,policy_id) REFERENCES public.consumer_consent_policies(tenant_id,id)",
    )
    _add_not_valid(
        "fk_consent_records_tenant_public_id",
        "consent_records",
        "FOREIGN KEY (tenant_id,public_id) REFERENCES public.code_items(tenant_id,public_id)",
    )
    _add_not_valid(
        "fk_consumer_consent_actions_tenant_consent",
        "consumer_consent_actions",
        "FOREIGN KEY (tenant_id,consent_id) REFERENCES public.consent_records(tenant_id,id)",
    )
    _add_not_valid(
        "fk_consumer_consent_actions_tenant_consumer",
        "consumer_consent_actions",
        "FOREIGN KEY (tenant_id,consumer_id) REFERENCES public.consumer_profiles(tenant_id,id)",
    )
    _add_not_valid(
        "fk_consumer_profiles_tenant_lead_consent",
        "consumer_profiles",
        "FOREIGN KEY (tenant_id,lead_consent_id) REFERENCES public.consent_records(tenant_id,id)",
    )
    _add_not_valid(
        "ck_consent_records_status",
        "consent_records",
        "CHECK (status IN ('granted','withdrawn'))",
    )
    _add_not_valid(
        "ck_consent_records_authority_provenance",
        "consent_records",
        "CHECK (authority_version=0 OR (authority_version=1 AND policy_id IS NOT NULL AND purpose IS NOT NULL "
        "AND policy_digest IS NOT NULL AND visitor_subject_hash IS NOT NULL AND scan_event_id IS NOT NULL "
        "AND scan_event_time IS NOT NULL AND idempotency_key IS NOT NULL))",
    )
    _add_not_valid(
        "ck_consumer_profiles_lead_provenance",
        "consumer_profiles",
        "CHECK (lead_consent_id IS NULL OR (lead_scan_event_id IS NOT NULL AND lead_scan_event_time IS NOT NULL "
        "AND lead_captured_at IS NOT NULL))",
    )
    op.execute(
        "ALTER TABLE public.consent_records DROP CONSTRAINT IF EXISTS consent_records_consumer_id_fkey"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if bind.execute(sa.text("SELECT EXISTS(SELECT 1 FROM consent_records WHERE authority_version=1)")).scalar_one():
        raise RuntimeError("u6e2 downgrade blocked: authoritative consent facts exist")
    for name, table in (
        ("ck_consumer_profiles_lead_provenance", "consumer_profiles"),
        ("ck_consent_records_authority_provenance", "consent_records"),
        ("ck_consent_records_status", "consent_records"),
        ("fk_consumer_profiles_tenant_lead_consent", "consumer_profiles"),
        ("fk_consumer_consent_actions_tenant_consumer", "consumer_consent_actions"),
        ("fk_consumer_consent_actions_tenant_consent", "consumer_consent_actions"),
        ("fk_consent_records_tenant_public_id", "consent_records"),
        ("fk_consent_records_tenant_policy", "consent_records"),
        ("fk_consent_records_tenant_consumer", "consent_records"),
        ("fk_consent_records_tenant", "consent_records"),
    ):
        op.drop_constraint(name, table, type_="foreignkey" if name.startswith("fk_") else "check")
    op.create_foreign_key(
        "consent_records_consumer_id_fkey", "consent_records", "consumer_profiles", ["consumer_id"], ["id"]
    )
    op.drop_constraint("uq_consent_records_tenant_id", "consent_records", type_="unique")
    op.drop_constraint("uq_consumer_profiles_tenant_id", "consumer_profiles", type_="unique")
