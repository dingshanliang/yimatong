"""seed wechat_benefit_delivery consent policy

为外部权益发放（youzan openid / weimob 手机号桥接）补齐 `wechat_benefit_delivery`
consent purpose 的策略种子：worker 已按该 scenario 读取同意（campaign_claim_worker），
但公开授予端点要求 consumer_consent_policies 存在对应策略行，此前无任何授予路径。

Revision ID: a34cfb8027f0
Revises: f619a09be4c0
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a34cfb8027f0"
down_revision: str | Sequence[str] | None = "f619a09be4c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PURPOSE = "wechat_benefit_delivery"
_POLICY_VERSION = "2026-09-15-v1"

_POLICY_VALUE = """
    ('wechat_benefit_delivery','data_share','2026-09-15-v1','外部权益发放授权',
     '经您主动同意后，我们仅为发放您已领取的外部平台权益（如优惠券），使用受保护的微信身份标识或手机号与外部平台交互完成发放，并按照交易、审计和合规要求保留必要事实。')
"""

# a20b21c22d23 落库的当前权威种子函数体（3 基础 purpose + brand_membership），
# 本迁移仅在其 VALUES 追加 wechat_benefit_delivery 行
_SEED_FUNCTION = r"""
CREATE OR REPLACE FUNCTION public.seed_consumer_consent_policies_for_tenant() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE policy_row record;
BEGIN
  FOR policy_row IN SELECT * FROM (VALUES
    ('privacy_policy','privacy','2026-07-27-v1','隐私政策与个人信息处理告知',
     '我们仅在提供扫码溯源、会员与权益服务所必需的范围内处理个人信息。您可随时撤回同意；撤回不影响此前处理的合法性。'),
    ('lead_capture','marketing','2026-07-27-v1','联系信息收集与营销沟通同意',
     '经您主动同意后，我们会保存您提交的联系方式、称呼、地区和意向，用于品牌咨询与后续沟通。您可随时撤回，撤回后联系信息将被抑制并删除可恢复的线索字段。'),
    ('wechat_cash_payout','data_share','2026-08-11-v1','微信现金权益发放授权',
     '经您主动同意后，我们仅为发放已领取的现金权益使用受保护的微信身份标识，并按照交易、审计和合规要求保留必要事实。'),
    ('brand_membership','privacy','2026-08-20-v1','品牌会员服务协议与个人信息处理同意',
     '经您主动勾选同意后，我们将建立并管理您的品牌会员关系，用于识别会员身份、保存权益和提供会员服务。营销消息将另行征得同意，不因加入会员而自动授权。'),
    ('wechat_benefit_delivery','data_share','2026-09-15-v1','外部权益发放授权',
     '经您主动同意后，我们仅为发放您已领取的外部平台权益（如优惠券），使用受保护的微信身份标识或手机号与外部平台交互完成发放，并按照交易、审计和合规要求保留必要事实。')
  ) AS defaults(purpose,consent_type,policy_version,policy_title,policy_content)
  LOOP
    INSERT INTO public.consumer_consent_policies(id,tenant_id,purpose,consent_type,policy_version,
      policy_digest,policy_title,policy_content,effective_at)
    VALUES(gen_random_uuid(),NEW.id,policy_row.purpose,policy_row.consent_type,policy_row.policy_version,
      encode(digest(convert_to(policy_row.policy_content,'UTF8'),'sha256'),'hex'),policy_row.policy_title,
      policy_row.policy_content,statement_timestamp()) RETURNING * INTO policy_row;
    INSERT INTO public.consumer_consent_policy_current(tenant_id,purpose,policy_id)
    VALUES(NEW.id,policy_row.purpose,policy_row.id);
  END LOOP;
  RETURN NEW;
END $f$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # 1) 新租户播种函数纳入新 purpose
    op.execute(_SEED_FUNCTION)
    # 2) 存量租户回填策略行与 current 指针
    op.execute(
        f"""
        INSERT INTO public.consumer_consent_policies(
          id,tenant_id,purpose,consent_type,policy_version,policy_digest,policy_title,policy_content,effective_at
        )
        SELECT gen_random_uuid(),tenant.id,defaults.purpose,defaults.consent_type,defaults.policy_version,
          encode(digest(convert_to(defaults.policy_content,'UTF8'),'sha256'),'hex'),defaults.policy_title,
          defaults.policy_content,statement_timestamp()
        FROM public.tenants AS tenant
        CROSS JOIN (VALUES {_POLICY_VALUE})
          AS defaults(purpose,consent_type,policy_version,policy_title,policy_content)
        ON CONFLICT (tenant_id,purpose,policy_version) DO NOTHING;
        """
    )
    op.execute(
        f"""
        INSERT INTO public.consumer_consent_policy_current(tenant_id,purpose,policy_id)
        SELECT policy.tenant_id,policy.purpose,policy.id
        FROM public.consumer_consent_policies AS policy
        WHERE policy.purpose='{_PURPOSE}' AND policy.policy_version='{_POLICY_VERSION}'
        ON CONFLICT (tenant_id,purpose) DO NOTHING;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # 有已授权事实时拒绝降级（策略行被 consent_records 外键依赖，删除即破坏审计链）
    facts = bind.execute(sa.text(f"SELECT count(*) FROM consent_records WHERE purpose='{_PURPOSE}'")).scalar_one()
    if facts:
        raise RuntimeError(
            "a34cfb8027f0 downgrade blocked: wechat_benefit_delivery consent facts exist; archive before downgrade"
        )
    # 3) 种子函数还原为 a20b21c22d23 版本（4 purpose，无 wechat_benefit_delivery）
    op.execute(
        r"""
CREATE OR REPLACE FUNCTION public.seed_consumer_consent_policies_for_tenant() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
DECLARE policy_row record;
BEGIN
  FOR policy_row IN SELECT * FROM (VALUES
    ('privacy_policy','privacy','2026-07-27-v1','隐私政策与个人信息处理告知',
     '我们仅在提供扫码溯源、会员与权益服务所必需的范围内处理个人信息。您可随时撤回同意；撤回不影响此前处理的合法性。'),
    ('lead_capture','marketing','2026-07-27-v1','联系信息收集与营销沟通同意',
     '经您主动同意后，我们会保存您提交的联系方式、称呼、地区和意向，用于品牌咨询与后续沟通。您可随时撤回，撤回后联系信息将被抑制并删除可恢复的线索字段。'),
    ('wechat_cash_payout','data_share','2026-08-11-v1','微信现金权益发放授权',
     '经您主动同意后，我们仅为发放已领取的现金权益使用受保护的微信身份标识，并按照交易、审计和合规要求保留必要事实。'),
    ('brand_membership','privacy','2026-08-20-v1','品牌会员服务协议与个人信息处理同意',
     '经您主动勾选同意后，我们将建立并管理您的品牌会员关系，用于识别会员身份、保存权益和提供会员服务。营销消息将另行征得同意，不因加入会员而自动授权。')
  ) AS defaults(purpose,consent_type,policy_version,policy_title,policy_content)
  LOOP
    INSERT INTO public.consumer_consent_policies(id,tenant_id,purpose,consent_type,policy_version,
      policy_digest,policy_title,policy_content,effective_at)
    VALUES(gen_random_uuid(),NEW.id,policy_row.purpose,policy_row.consent_type,policy_row.policy_version,
      encode(digest(convert_to(policy_row.policy_content,'UTF8'),'sha256'),'hex'),policy_row.policy_title,
      policy_row.policy_content,statement_timestamp()) RETURNING * INTO policy_row;
    INSERT INTO public.consumer_consent_policy_current(tenant_id,purpose,policy_id)
    VALUES(NEW.id,policy_row.purpose,policy_row.id);
  END LOOP;
  RETURN NEW;
END $f$;
"""
    )
    # 不可变守卫拦截 DELETE，按 a20b21c22d23 降级顺序先摘除再重建
    op.execute("DROP TRIGGER trg_guard_immutable_consumer_consent_policy ON public.consumer_consent_policies")
    op.execute(f"DELETE FROM public.consumer_consent_policy_current WHERE purpose='{_PURPOSE}'")
    op.execute(f"DELETE FROM public.consumer_consent_policies WHERE purpose='{_PURPOSE}'")
    op.execute(
        """
        CREATE TRIGGER trg_guard_immutable_consumer_consent_policy BEFORE UPDATE OR DELETE
        ON public.consumer_consent_policies FOR EACH ROW
        EXECUTE FUNCTION public.guard_immutable_consumer_consent_policy()
        """
    )
