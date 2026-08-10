"""Enforce atomic code-batch generation and delivery manifests.

Revision ID: 3f91c0d2e4a6
Revises: 2ed1cb06d0ca
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3f91c0d2e4a6"
down_revision: str | None = "2ed1cb06d0ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_BATCH_GUARD = "guard_code_batch_delivery_contract"
_ITEM_GUARD = "guard_code_item_batch_contract"
_RECEIPT_GUARD = "guard_code_batch_generation_receipt"
_EXPORT_GUARD = "guard_code_export_manifest"
_ITEM_FINAL_STATE_GUARD = "enforce_code_item_parent_final_state"
_ARTIFACT_FUNCTION = "get_code_export_artifact"
_ROLLOUT_STATE_TABLE = "code_delivery_contract_rollout_state"
_CHINA_BUSINESS_DATE_SQL = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::date"
_MAX_CODE_CSV_ARTIFACT_BYTES = 16 * 1024 * 1024
_EXPORT_SAFE_SELECT_COLUMNS = (
    "id",
    "tenant_id",
    "account_id",
    "export_type",
    "resource_id",
    "file_name",
    "row_count",
    "status",
    "code_batch_id",
    "manifest_version",
    "checksum_sha256",
    "artifact_size_bytes",
    "created_at",
    "updated_at",
)


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": _RUNTIME_ROLE})
        .scalar_one()
    )


def _create_rollout_state() -> None:
    # Platform-global migration control, intentionally outside the ORM and
    # tenant RLS registry.  An empty table means expand/coexistence; the
    # singleton row is inserted only by the finalize revision after old
    # application writers have drained.
    op.create_table(
        _ROLLOUT_STATE_TABLE,
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_by", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_code_delivery_contract_rollout_state_singleton"),
        sa.CheckConstraint("phase = 'finalized'", name="ck_code_delivery_contract_rollout_state_phase"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(f"REVOKE ALL ON TABLE public.{_ROLLOUT_STATE_TABLE} FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"REVOKE ALL ON TABLE public.{_ROLLOUT_STATE_TABLE} FROM {_RUNTIME_ROLE}")


def _add_columns() -> None:
    op.add_column("code_batches", sa.Column("contract_version", sa.SmallInteger(), nullable=True))
    op.add_column("code_batches", sa.Column("source", sa.String(length=16), nullable=True))
    op.add_column("code_batches", sa.Column("expected_item_count", sa.Integer(), nullable=True))
    op.add_column("code_batches", sa.Column("export_manifest_id", sa.Uuid(), nullable=True))
    op.add_column("code_batches", sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("code_batches", sa.Column("printing_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("code_batches", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("code_batches", sa.Column("delivery_recipient", sa.String(length=255), nullable=True))

    op.add_column("export_logs", sa.Column("code_batch_id", sa.Uuid(), nullable=True))
    op.add_column("export_logs", sa.Column("manifest_version", sa.Integer(), nullable=True))
    op.add_column("export_logs", sa.Column("checksum_sha256", sa.String(length=64), nullable=True))
    op.add_column("export_logs", sa.Column("artifact_size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("export_logs", sa.Column("artifact_ciphertext", sa.LargeBinary(), nullable=True))
    op.add_column("export_logs", sa.Column("artifact_nonce", sa.LargeBinary(), nullable=True))
    op.add_column("export_logs", sa.Column("artifact_scheme", sa.String(length=32), nullable=True))
    op.add_column("export_logs", sa.Column("artifact_key_id", sa.String(length=64), nullable=True))

    op.execute(
        """
        UPDATE public.code_batches AS batch
        SET contract_version = 0,
            source = 'generated',
            expected_item_count = (
                SELECT count(*)::integer
                FROM public.code_items AS item
                WHERE item.tenant_id = batch.tenant_id
                  AND item.code_batch_id = batch.id
            )
        """
    )
    op.alter_column("code_batches", "contract_version", existing_type=sa.SmallInteger(), nullable=False)
    op.alter_column("code_batches", "source", existing_type=sa.String(length=16), nullable=False)
    op.alter_column("code_batches", "expected_item_count", existing_type=sa.Integer(), nullable=False)
    op.alter_column("code_batches", "contract_version", server_default=sa.text("0"))
    op.alter_column("code_batches", "source", server_default=sa.text("'generated'"))

    op.create_index("ix_code_batches_export_manifest_id", "code_batches", ["export_manifest_id"], unique=False)
    op.create_index("ix_export_logs_code_batch_id", "export_logs", ["code_batch_id"], unique=False)


def _create_receipts() -> None:
    op.create_table(
        "code_batch_generation_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_digest", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("code_batch_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_code_batch_generation_receipts_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_code_batch_generation_receipts_tenant_creator",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_digest", name="uq_code_batch_generation_receipts_tenant_idempotency"
        ),
    )
    op.create_index(
        "ix_code_batch_generation_receipts_tenant_id",
        "code_batch_generation_receipts",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_code_batch_generation_receipts_tenant_batch",
        "code_batch_generation_receipts",
        ["tenant_id", "code_batch_id"],
        unique=False,
    )


def _add_contracts() -> None:
    op.create_unique_constraint("uq_export_logs_tenant_id_id", "export_logs", ["tenant_id", "id"])
    op.create_index(
        "uq_export_logs_tenant_code_batch_version",
        "export_logs",
        ["tenant_id", "code_batch_id", "manifest_version"],
        unique=True,
        postgresql_where=sa.text("code_batch_id IS NOT NULL AND manifest_version IS NOT NULL"),
    )

    foreign_keys = (
        (
            "export_logs",
            "fk_export_logs_tenant",
            "tenant_id",
            "tenants",
            "id",
        ),
        (
            "export_logs",
            "fk_export_logs_tenant_code_batch",
            "tenant_id, code_batch_id",
            "code_batches",
            "tenant_id, id",
        ),
        (
            "code_batches",
            "fk_code_batches_tenant_export_manifest",
            "tenant_id, export_manifest_id",
            "export_logs",
            "tenant_id, id",
        ),
        (
            "code_batch_generation_receipts",
            "fk_code_batch_generation_receipts_tenant_code_batch",
            "tenant_id, code_batch_id",
            "code_batches",
            "tenant_id, id",
        ),
    )
    for table, name, local, parent, remote in foreign_keys:
        op.execute(
            f"ALTER TABLE public.{table} ADD CONSTRAINT {name} FOREIGN KEY ({local}) "
            f"REFERENCES public.{parent} ({remote}) NOT VALID"
        )
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")

    checks = (
        (
            "code_batches",
            "ck_code_batches_contract_version",
            "contract_version IN (0, 1)",
        ),
        ("code_batches", "ck_code_batches_source", "source IN ('generated', 'imported')"),
        (
            "code_batches",
            "ck_code_batches_expected_item_count_cap",
            "contract_version = 0 OR expected_item_count BETWEEN 1 AND 10000",
        ),
        (
            "code_batches",
            "ck_code_batches_generation_shape",
            """
            contract_version = 0 OR (
                (source = 'generated' AND generation_mode = 'batch_level' AND code_type = 'single'
                 AND quantity = 1 AND expected_item_count = 1)
                OR
                (source = 'generated' AND generation_mode = 'item_level' AND code_type = 'single'
                 AND quantity > 0 AND expected_item_count = quantity)
                OR
                (source = 'generated' AND generation_mode = 'item_level' AND code_type = 'paired'
                 AND quantity > 0 AND expected_item_count = quantity * 2)
                OR
                (source = 'imported' AND generation_mode = 'item_level' AND code_type = 'single'
                 AND quantity > 0 AND expected_item_count = quantity)
            )
            """,
        ),
        (
            "code_batches",
            "ck_code_batches_delivery_timestamps",
            """
            (printing_at IS NULL OR (exported_at IS NOT NULL AND printing_at >= exported_at))
            AND (delivered_at IS NULL OR (printing_at IS NOT NULL AND delivered_at >= printing_at))
            """,
        ),
        (
            "code_batches",
            "ck_code_batches_delivery_recipient",
            """
            (delivered_at IS NULL AND delivery_recipient IS NULL)
            OR (delivered_at IS NOT NULL AND NULLIF(btrim(delivery_recipient), '') IS NOT NULL)
            """,
        ),
        (
            "code_batch_generation_receipts",
            "ck_code_batch_generation_receipts_idempotency_digest",
            "idempotency_digest ~ '^[0-9a-f]{64}$'",
        ),
        (
            "code_batch_generation_receipts",
            "ck_code_batch_generation_receipts_request_fingerprint",
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
        ),
        (
            "export_logs",
            "ck_export_logs_manifest_shape",
            f"""
            (manifest_version IS NULL
             AND artifact_ciphertext IS NULL
             AND artifact_nonce IS NULL
             AND artifact_scheme IS NULL
             AND artifact_key_id IS NULL)
            OR (
                manifest_version > 0
                AND row_count BETWEEN 1 AND 10000
                AND checksum_sha256 ~ '^[0-9a-f]{{64}}$'
                AND artifact_size_bytes BETWEEN 1 AND {_MAX_CODE_CSV_ARTIFACT_BYTES}
                AND artifact_ciphertext IS NOT NULL
                AND octet_length(artifact_ciphertext) = artifact_size_bytes + 16
                AND artifact_nonce IS NOT NULL
                AND octet_length(artifact_nonce) = 12
                AND artifact_scheme = 'aes-256-gcm-v1'
                AND artifact_key_id ~ '^[A-Za-z0-9._:-]{{1,64}}$'
            )
            """,
        ),
        (
            "export_logs",
            "ck_export_logs_code_csv_manifest",
            """
            export_type <> 'code_csv' OR manifest_version IS NULL OR (
                code_batch_id IS NOT NULL
                AND resource_id = code_batch_id
                AND status = 'completed'
            )
            """,
        ),
    )
    for table, name, expression in checks:
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} CHECK ({expression}) NOT VALID")
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")


def _install_batch_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_BATCH_GUARD}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            actual_count integer;
            manifest_valid boolean;
            rollout_finalized boolean;
            privileged_legacy boolean := has_parameter_privilege(session_user, 'app.bypass_rls', 'SET');
        BEGIN
            SELECT EXISTS (
                SELECT 1 FROM public.{_ROLLOUT_STATE_TABLE}
                WHERE id = 1 AND phase = 'finalized'
            ) INTO rollout_finalized;

            IF TG_OP = 'DELETE' THEN
                IF privileged_legacy THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'code batch deletion is forbidden';
            END IF;

            IF NEW.expected_item_count IS NULL THEN
                NEW.expected_item_count := CASE
                    WHEN NEW.generation_mode = 'batch_level' THEN 1
                    WHEN NEW.code_type = 'paired' THEN NEW.quantity * 2
                    ELSE NEW.quantity
                END;
            END IF;

            IF TG_OP = 'INSERT' AND NEW.contract_version = 0 THEN
                IF rollout_finalized AND NOT privileged_legacy THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '42501', MESSAGE = 'runtime must use code batch contract version 1';
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'UPDATE' THEN
                IF OLD.contract_version = 0 AND NEW.contract_version = 0 THEN
                    IF rollout_finalized AND NOT privileged_legacy THEN
                        RAISE EXCEPTION USING
                            ERRCODE = '42501', MESSAGE = 'legacy code batch is read-only after rollout finalization';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.product_id IS DISTINCT FROM OLD.product_id
                   OR NEW.sku_id IS DISTINCT FROM OLD.sku_id
                   OR NEW.production_batch_id IS DISTINCT FROM OLD.production_batch_id
                   OR NEW.batch_code IS DISTINCT FROM OLD.batch_code
                   OR NEW.quantity IS DISTINCT FROM OLD.quantity
                   OR NEW.code_type IS DISTINCT FROM OLD.code_type
                   OR NEW.generation_mode IS DISTINCT FROM OLD.generation_mode
                   OR NEW.source IS DISTINCT FROM OLD.source
                   OR NEW.expected_item_count IS DISTINCT FROM OLD.expected_item_count
                   OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
                    RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'code batch authoritative identity is immutable';
                END IF;

                IF OLD.contract_version = 1 AND NEW.contract_version IS DISTINCT FROM OLD.contract_version THEN
                    RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'code batch contract version is immutable';
                END IF;
                IF OLD.contract_version = 0 AND NEW.contract_version = 1
                   AND NOT (OLD.status::text = 'completed' AND NEW.status::text = 'exported') THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514', MESSAGE = 'legacy code batch may upgrade only during export';
                END IF;
            END IF;

            IF NEW.contract_version <> 1 THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid code batch contract version';
            END IF;
            IF TG_OP = 'INSERT' AND NEW.status::text NOT IN ('pending', 'generating') THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514', MESSAGE = 'new code batch must start pending or generating';
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status::text = 'pending' AND NEW.status::text = 'generating')
                OR (OLD.status::text = 'generating' AND NEW.status::text IN ('completed', 'failed'))
                OR (OLD.status::text = 'completed' AND NEW.status::text = 'exported')
                OR (OLD.status::text = 'exported' AND NEW.status::text = 'printing')
                OR (OLD.status::text = 'printing' AND NEW.status::text = 'delivered')
                OR (OLD.status::text = 'delivered' AND NEW.status::text = 'activated')
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'invalid code batch delivery transition';
            END IF;

            IF NEW.status::text IN ('pending', 'generating', 'completed', 'failed') THEN
                IF NEW.export_manifest_id IS NOT NULL OR NEW.exported_at IS NOT NULL
                   OR NEW.printing_at IS NOT NULL OR NEW.delivered_at IS NOT NULL
                   OR NEW.delivery_recipient IS NOT NULL THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'pre-export batch has delivery metadata';
                END IF;
            ELSIF NEW.status::text = 'exported' THEN
                IF NEW.export_manifest_id IS NULL OR NEW.exported_at IS NULL
                   OR NEW.printing_at IS NOT NULL OR NEW.delivered_at IS NOT NULL
                   OR NEW.delivery_recipient IS NOT NULL THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'exported batch metadata is incomplete';
                END IF;
            ELSIF NEW.status::text = 'printing' THEN
                IF NEW.export_manifest_id IS NULL OR NEW.exported_at IS NULL OR NEW.printing_at IS NULL
                   OR NEW.delivered_at IS NOT NULL OR NEW.delivery_recipient IS NOT NULL THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'printing batch metadata is incomplete';
                END IF;
            ELSIF NEW.status::text IN ('delivered', 'activated') THEN
                IF NEW.export_manifest_id IS NULL OR NEW.exported_at IS NULL OR NEW.printing_at IS NULL
                   OR NEW.delivered_at IS NULL OR NULLIF(btrim(NEW.delivery_recipient), '') IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'delivered batch metadata is incomplete';
                END IF;
            END IF;

            IF TG_OP = 'UPDATE' AND OLD.export_manifest_id IS NOT NULL
               AND (NEW.export_manifest_id IS DISTINCT FROM OLD.export_manifest_id
                    OR NEW.exported_at IS DISTINCT FROM OLD.exported_at
                    OR NEW.printing_at < OLD.printing_at
                    OR NEW.delivered_at < OLD.delivered_at
                    OR (OLD.delivery_recipient IS NOT NULL
                        AND NEW.delivery_recipient IS DISTINCT FROM OLD.delivery_recipient)) THEN
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'code batch delivery evidence is immutable';
            END IF;

            IF TG_OP = 'INSERT'
               OR NEW.status IS DISTINCT FROM OLD.status
               OR NEW.contract_version IS DISTINCT FROM OLD.contract_version THEN
                PERFORM production_batch.id
                FROM public.production_batches AS production_batch
                WHERE production_batch.tenant_id = NEW.tenant_id
                  AND production_batch.product_id = NEW.product_id
                  AND production_batch.sku_id = NEW.sku_id
                  AND production_batch.id = NEW.production_batch_id
                  AND production_batch.status::text = 'active'
                  AND production_batch.expiry_date >= {_CHINA_BUSINESS_DATE_SQL}
                FOR SHARE OF production_batch NOWAIT;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING ERRCODE = '23514',
                        MESSAGE = 'code batch requires an active authoritative production batch';
                END IF;
            END IF;

            IF TG_OP = 'UPDATE' AND OLD.status::text = 'generating' AND NEW.status::text = 'completed' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM public.code_batch_generation_receipts AS receipt
                    WHERE receipt.tenant_id = NEW.tenant_id
                      AND receipt.code_batch_id = NEW.id
                      AND receipt.created_by = NEW.created_by
                ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514', MESSAGE = 'completed code batch requires its generation receipt';
                END IF;
                SELECT count(*)::integer INTO actual_count
                FROM public.code_items AS item
                WHERE item.tenant_id = NEW.tenant_id AND item.code_batch_id = NEW.id;
                IF actual_count <> NEW.expected_item_count THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514', MESSAGE = 'code batch item count does not match expected count';
                END IF;
                IF NEW.code_type = 'paired' THEN
                    IF EXISTS (
                        SELECT 1
                        FROM public.code_items AS item
                        WHERE item.tenant_id = NEW.tenant_id AND item.code_batch_id = NEW.id
                        GROUP BY item.pair_id
                        HAVING item.pair_id IS NULL OR count(*) <> 2
                           OR count(*) FILTER (WHERE item.code_type = 'outer') <> 1
                           OR count(*) FILTER (WHERE item.code_type = 'inner') <> 1
                           OR count(*) FILTER (WHERE item.status::text = 'created') <> 2
                    ) THEN
                        RAISE EXCEPTION USING
                            ERRCODE = '23514', MESSAGE = 'paired code batch has invalid pair metadata';
                    END IF;
                ELSIF EXISTS (
                    SELECT 1 FROM public.code_items AS item
                    WHERE item.tenant_id = NEW.tenant_id AND item.code_batch_id = NEW.id
                      AND (item.code_type <> 'single' OR item.pair_id IS NOT NULL OR item.status::text <> 'created')
                ) THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'single code batch has invalid item metadata';
                END IF;
            END IF;

            IF NEW.status::text IN ('exported', 'printing', 'delivered', 'activated') THEN
                SELECT EXISTS (
                    SELECT 1 FROM public.export_logs AS manifest
                    WHERE manifest.tenant_id = NEW.tenant_id
                      AND manifest.id = NEW.export_manifest_id
                      AND manifest.code_batch_id = NEW.id
                      AND manifest.resource_id = NEW.id
                      AND manifest.export_type = 'code_csv'
                      AND manifest.status = 'completed'
                      AND manifest.manifest_version > 0
                      AND manifest.row_count = NEW.expected_item_count
                      AND manifest.checksum_sha256 ~ '^[0-9a-f]{{64}}$'
                      AND manifest.artifact_size_bytes BETWEEN 1 AND {_MAX_CODE_CSV_ARTIFACT_BYTES}
                      AND manifest.artifact_ciphertext IS NOT NULL
                      AND octet_length(manifest.artifact_ciphertext) = manifest.artifact_size_bytes + 16
                      AND octet_length(manifest.artifact_nonce) = 12
                      AND manifest.artifact_scheme = 'aes-256-gcm-v1'
                      AND manifest.artifact_key_id ~ '^[A-Za-z0-9._:-]{{1,64}}$'
                ) INTO manifest_valid;
                IF NOT manifest_valid THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'code batch export manifest is invalid';
                END IF;
            END IF;

            IF TG_OP = 'UPDATE' AND OLD.status::text = 'delivered' AND NEW.status::text = 'activated'
               AND EXISTS (
                   SELECT 1 FROM public.code_items AS item
                   WHERE item.tenant_id = NEW.tenant_id AND item.code_batch_id = NEW.id
                     AND item.status::text <> 'activated'
               ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514', MESSAGE = 'all delivered code items must be activated atomically';
            END IF;
            RETURN NEW;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING
                ERRCODE = '55P03', MESSAGE = 'authoritative production batch is concurrently changing';
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_BATCH_GUARD}() FROM PUBLIC")
    op.execute(
        f"CREATE TRIGGER trg_guard_code_batch_delivery_contract "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.code_batches "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_BATCH_GUARD}()"
    )


def _install_item_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_ITEM_GUARD}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE batch_row public.code_batches%ROWTYPE;
        DECLARE rollout_finalized boolean;
        DECLARE privileged_legacy boolean := has_parameter_privilege(session_user, 'app.bypass_rls', 'SET');
        BEGIN
            IF TG_OP = 'DELETE' THEN
                SELECT * INTO batch_row FROM public.code_batches
                WHERE tenant_id = OLD.tenant_id AND id = OLD.code_batch_id FOR UPDATE;
                IF batch_row.contract_version = 0 AND privileged_legacy THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'code item deletion is forbidden';
            END IF;
            SELECT * INTO batch_row FROM public.code_batches
            WHERE tenant_id = NEW.tenant_id AND id = NEW.code_batch_id FOR UPDATE NOWAIT;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'code item batch is unavailable';
            END IF;
            IF batch_row.contract_version = 0 THEN
                SELECT EXISTS (
                    SELECT 1 FROM public.{_ROLLOUT_STATE_TABLE}
                    WHERE id = 1 AND phase = 'finalized'
                ) INTO rollout_finalized;
                IF rollout_finalized AND NOT privileged_legacy THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '42501', MESSAGE = 'legacy code batch items are read-only after rollout finalization';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF batch_row.status::text <> 'generating' OR NEW.status::text <> 'created' THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514', MESSAGE = 'code items may be created only in a generating batch';
                END IF;
                IF batch_row.code_type = 'paired' THEN
                    IF NEW.code_type NOT IN ('outer', 'inner') OR NEW.pair_id IS NULL THEN
                        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'paired code item metadata is invalid';
                    END IF;
                ELSIF NEW.code_type <> 'single' OR NEW.pair_id IS NOT NULL THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'single code item metadata is invalid';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.code_batch_id IS DISTINCT FROM OLD.code_batch_id
               OR NEW.public_id IS DISTINCT FROM OLD.public_id
               OR NEW.code_type IS DISTINCT FROM OLD.code_type
               OR NEW.pair_id IS DISTINCT FROM OLD.pair_id THEN
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'code item identity is immutable';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status::text = 'created' AND NEW.status::text IN ('activated', 'revoked'))
                OR (OLD.status::text = 'activated' AND NEW.status::text IN ('bound', 'revoked', 'frozen'))
                OR (OLD.status::text = 'bound' AND NEW.status::text IN ('expired', 'revoked', 'frozen'))
                OR (OLD.status::text = 'frozen' AND NEW.status::text IN ('activated', 'revoked'))
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'invalid code item lifecycle transition';
            END IF;
            IF OLD.status::text = 'created' AND NEW.status::text = 'activated'
               AND batch_row.status::text <> 'delivered' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514', MESSAGE = 'code item activation requires a delivered code batch';
            END IF;
            IF OLD.status::text = 'activated' AND NEW.status::text = 'bound'
               AND batch_row.status::text <> 'activated' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514', MESSAGE = 'code item binding requires an activated code batch';
            END IF;
            IF OLD.status::text = 'frozen' AND NEW.status::text = 'activated'
               AND batch_row.status::text <> 'activated' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514', MESSAGE = 'code item reactivation requires an activated code batch';
            END IF;
            RETURN NEW;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING
                ERRCODE = '55P03', MESSAGE = 'authoritative code batch is concurrently changing';
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ITEM_GUARD}() FROM PUBLIC")
    op.execute(
        f"CREATE TRIGGER trg_guard_code_item_batch_contract "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.code_items "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_ITEM_GUARD}()"
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_ITEM_FINAL_STATE_GUARD}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE parent_status text;
        BEGIN
            IF OLD.status::text = 'created' AND NEW.status::text = 'activated' THEN
                SELECT batch.status::text INTO parent_status
                FROM public.code_batches AS batch
                WHERE batch.tenant_id = NEW.tenant_id AND batch.id = NEW.code_batch_id;
                IF parent_status IS DISTINCT FROM 'activated' THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'activated code item requires its code batch to finish activated';
                END IF;
            END IF;
            RETURN NULL;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ITEM_FINAL_STATE_GUARD}() FROM PUBLIC")
    op.execute(
        f"CREATE CONSTRAINT TRIGGER trg_enforce_code_item_parent_final_state "
        f"AFTER UPDATE OF status ON public.code_items DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_ITEM_FINAL_STATE_GUARD}()"
    )


def _install_receipt_and_export_guards() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_RECEIPT_GUARD}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE actor_matches boolean;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'code generation receipt deletion is forbidden';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.created_by IS DISTINCT FROM OLD.created_by
                   OR NEW.idempotency_digest IS DISTINCT FROM OLD.idempotency_digest
                   OR NEW.request_fingerprint IS DISTINCT FROM OLD.request_fingerprint
                   OR OLD.code_batch_id IS NOT NULL
                   OR NEW.code_batch_id IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'code generation receipt is immutable';
                END IF;
            ELSIF NEW.code_batch_id IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'new code generation receipt must be unclaimed';
            END IF;
            IF NEW.code_batch_id IS NOT NULL THEN
                SELECT EXISTS (
                    SELECT 1 FROM public.code_batches AS batch
                    WHERE batch.tenant_id = NEW.tenant_id
                      AND batch.id = NEW.code_batch_id
                      AND batch.created_by = NEW.created_by
                ) INTO actor_matches;
                IF NOT actor_matches THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23503', MESSAGE = 'generation receipt actor does not own the code batch';
                END IF;
            END IF;
            IF NEW.idempotency_digest !~ '^[0-9a-f]{{64}}$'
               OR NEW.request_fingerprint !~ '^[0-9a-f]{{64}}$' THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid code generation idempotency material';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_RECEIPT_GUARD}() FROM PUBLIC")
    op.execute(
        f"CREATE TRIGGER trg_guard_code_batch_generation_receipt "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.code_batch_generation_receipts "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_RECEIPT_GUARD}()"
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_EXPORT_GUARD}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE batch_expected integer;
        DECLARE rollout_finalized boolean;
        DECLARE privileged_legacy boolean := has_parameter_privilege(session_user, 'app.bypass_rls', 'SET');
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF privileged_legacy AND OLD.manifest_version IS NULL THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'export manifest deletion is forbidden';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.manifest_version IS NOT NULL THEN
                IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.account_id IS DISTINCT FROM OLD.account_id
                   OR NEW.export_type IS DISTINCT FROM OLD.export_type
                   OR NEW.resource_id IS DISTINCT FROM OLD.resource_id
                   OR NEW.file_name IS DISTINCT FROM OLD.file_name OR NEW.row_count IS DISTINCT FROM OLD.row_count
                   OR NEW.status IS DISTINCT FROM OLD.status OR NEW.code_batch_id IS DISTINCT FROM OLD.code_batch_id
                   OR NEW.manifest_version IS DISTINCT FROM OLD.manifest_version
                   OR NEW.checksum_sha256 IS DISTINCT FROM OLD.checksum_sha256
                   OR NEW.artifact_size_bytes IS DISTINCT FROM OLD.artifact_size_bytes
                   OR NEW.artifact_ciphertext IS DISTINCT FROM OLD.artifact_ciphertext
                   OR NEW.artifact_nonce IS DISTINCT FROM OLD.artifact_nonce
                   OR NEW.artifact_scheme IS DISTINCT FROM OLD.artifact_scheme
                   OR NEW.artifact_key_id IS DISTINCT FROM OLD.artifact_key_id THEN
                    RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'code export manifest is immutable';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.export_type = 'code_csv' THEN
                IF NEW.manifest_version IS NULL THEN
                    SELECT EXISTS (
                        SELECT 1 FROM public.{_ROLLOUT_STATE_TABLE}
                        WHERE id = 1 AND phase = 'finalized'
                    ) INTO rollout_finalized;
                    IF privileged_legacy OR NOT rollout_finalized THEN
                        RETURN NEW;
                    END IF;
                    RAISE EXCEPTION USING
                        ERRCODE = '42501', MESSAGE = 'runtime code export requires a versioned manifest';
                END IF;
                SELECT expected_item_count INTO batch_expected
                FROM public.code_batches
                WHERE tenant_id = NEW.tenant_id AND id = NEW.code_batch_id AND status::text = 'completed'
                FOR SHARE;
                IF batch_expected IS NULL OR NEW.resource_id IS DISTINCT FROM NEW.code_batch_id
                   OR NEW.row_count IS DISTINCT FROM batch_expected OR NEW.status <> 'completed'
                   OR NEW.manifest_version <= 0 OR NEW.checksum_sha256 !~ '^[0-9a-f]{{64}}$'
                   OR NEW.artifact_size_bytes NOT BETWEEN 1 AND {_MAX_CODE_CSV_ARTIFACT_BYTES}
                   OR NEW.artifact_ciphertext IS NULL
                   OR octet_length(NEW.artifact_ciphertext) <> NEW.artifact_size_bytes + 16
                   OR NEW.artifact_nonce IS NULL OR octet_length(NEW.artifact_nonce) <> 12
                   OR NEW.artifact_scheme <> 'aes-256-gcm-v1'
                   OR NEW.artifact_key_id !~ '^[A-Za-z0-9._:-]{{1,64}}$'
                   OR NOT EXISTS (
                       SELECT 1 FROM public.accounts AS actor
                       WHERE actor.tenant_id = NEW.tenant_id AND actor.id = NEW.account_id
                   ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514', MESSAGE = 'code export manifest does not match completed batch';
                END IF;
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_EXPORT_GUARD}() FROM PUBLIC")
    op.execute(
        f"CREATE TRIGGER trg_guard_code_export_manifest "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.export_logs "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_EXPORT_GUARD}()"
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_ARTIFACT_FUNCTION}(
            requested_tenant_id uuid,
            requested_code_batch_id uuid,
            requested_manifest_id uuid
        ) RETURNS TABLE(
            artifact_ciphertext bytea,
            artifact_nonce bytea,
            artifact_scheme text,
            artifact_key_id text,
            artifact_size_bytes bigint,
            checksum_sha256 text,
            manifest_version integer,
            row_count integer
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF public.current_tenant_id() IS NULL
               OR requested_tenant_id IS DISTINCT FROM public.current_tenant_id() THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'code export artifact tenant context is invalid';
            END IF;
            RETURN QUERY
            SELECT manifest.artifact_ciphertext,
                   manifest.artifact_nonce,
                   manifest.artifact_scheme::text,
                   manifest.artifact_key_id::text,
                   manifest.artifact_size_bytes,
                   manifest.checksum_sha256::text,
                   manifest.manifest_version,
                   manifest.row_count
            FROM public.export_logs AS manifest
            WHERE manifest.tenant_id = requested_tenant_id
              AND manifest.code_batch_id = requested_code_batch_id
              AND manifest.id = requested_manifest_id
              AND manifest.export_type = 'code_csv'
              AND manifest.status = 'completed'
              AND manifest.manifest_version IS NOT NULL;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'code export artifact is unavailable';
            END IF;
        END
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_ARTIFACT_FUNCTION}(uuid, uuid, uuid) FROM PUBLIC"
    )


def _secure_receipts_and_acl() -> None:
    expression = (
        "tenant_id = public.current_tenant_id() OR ("
        "public.current_tenant_id() IS NULL "
        "AND current_setting('app.bypass_rls', true) = 'true' "
        "AND has_parameter_privilege(session_user, 'app.bypass_rls', 'SET'))"
    )
    op.execute("ALTER TABLE public.code_batch_generation_receipts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.code_batch_generation_receipts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON public.code_batch_generation_receipts "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
    if _runtime_role_exists():
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE ON TABLE public.code_batch_generation_receipts TO {_RUNTIME_ROLE}"
        )
        for table in ("code_batches", "code_items", "export_logs", "code_batch_generation_receipts"):
            op.execute(
                f"REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE public.{table} FROM {_RUNTIME_ROLE}"
            )
        safe_columns = ", ".join(_EXPORT_SAFE_SELECT_COLUMNS)
        op.execute(f"REVOKE SELECT ON TABLE public.export_logs FROM {_RUNTIME_ROLE}")
        op.execute(f"GRANT SELECT ({safe_columns}) ON TABLE public.export_logs TO {_RUNTIME_ROLE}")
        op.execute(
            f"GRANT EXECUTE ON FUNCTION public.{_ARTIFACT_FUNCTION}(uuid, uuid, uuid) TO {_RUNTIME_ROLE}"
        )


def _downgrade_preflight() -> None:
    op.execute(
        """
        DO $block$
        DECLARE protected_state text;
        BEGIN
            SELECT concat_ws(', ',
                CASE WHEN EXISTS (SELECT 1 FROM public.code_batches WHERE contract_version = 1)
                     THEN 'contract-v1 batches' END,
                CASE WHEN EXISTS (SELECT 1 FROM public.code_batch_generation_receipts)
                     THEN 'generation receipts' END,
                CASE WHEN EXISTS (SELECT 1 FROM public.export_logs WHERE manifest_version IS NOT NULL)
                     THEN 'export manifests' END)
            INTO protected_state;
            IF protected_state <> '' THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'Cannot discard atomic code delivery contract: ' || protected_state;
            END IF;
        END
        $block$
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    _create_rollout_state()
    _add_columns()
    _create_receipts()
    _add_contracts()
    _install_batch_guard()
    _install_item_guard()
    _install_receipt_and_export_guards()
    _secure_receipts_and_acl()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    _downgrade_preflight()

    op.execute("DROP TRIGGER trg_guard_code_export_manifest ON public.export_logs")
    op.execute(f"DROP FUNCTION public.{_EXPORT_GUARD}()")
    op.execute(f"DROP FUNCTION public.{_ARTIFACT_FUNCTION}(uuid, uuid, uuid)")
    op.execute("DROP TRIGGER trg_guard_code_batch_generation_receipt ON public.code_batch_generation_receipts")
    op.execute(f"DROP FUNCTION public.{_RECEIPT_GUARD}()")
    op.execute("DROP TRIGGER trg_guard_code_item_batch_contract ON public.code_items")
    op.execute(f"DROP FUNCTION public.{_ITEM_GUARD}()")
    op.execute("DROP TRIGGER trg_enforce_code_item_parent_final_state ON public.code_items")
    op.execute(f"DROP FUNCTION public.{_ITEM_FINAL_STATE_GUARD}()")
    op.execute("DROP TRIGGER trg_guard_code_batch_delivery_contract ON public.code_batches")
    op.execute(f"DROP FUNCTION public.{_BATCH_GUARD}()")

    if _runtime_role_exists():
        for table in ("code_batches", "code_items", "export_logs"):
            op.execute(f"GRANT DELETE ON TABLE public.{table} TO {_RUNTIME_ROLE}")
        op.execute(f"GRANT SELECT ON TABLE public.export_logs TO {_RUNTIME_ROLE}")

    op.drop_constraint("fk_code_batches_tenant_export_manifest", "code_batches", type_="foreignkey")
    op.drop_constraint("fk_export_logs_tenant_code_batch", "export_logs", type_="foreignkey")
    op.drop_constraint("fk_export_logs_tenant", "export_logs", type_="foreignkey")
    op.drop_constraint("ck_export_logs_code_csv_manifest", "export_logs", type_="check")
    op.drop_constraint("ck_export_logs_manifest_shape", "export_logs", type_="check")
    op.drop_index("uq_export_logs_tenant_code_batch_version", table_name="export_logs")
    op.drop_constraint("uq_export_logs_tenant_id_id", "export_logs", type_="unique")

    op.drop_table("code_batch_generation_receipts")
    op.drop_constraint("ck_code_batches_delivery_recipient", "code_batches", type_="check")
    op.drop_constraint("ck_code_batches_delivery_timestamps", "code_batches", type_="check")
    op.drop_constraint("ck_code_batches_generation_shape", "code_batches", type_="check")
    op.drop_constraint("ck_code_batches_expected_item_count_cap", "code_batches", type_="check")
    op.drop_constraint("ck_code_batches_source", "code_batches", type_="check")
    op.drop_constraint("ck_code_batches_contract_version", "code_batches", type_="check")

    op.drop_table(_ROLLOUT_STATE_TABLE)

    op.drop_index("ix_export_logs_code_batch_id", table_name="export_logs")
    op.drop_column("export_logs", "artifact_key_id")
    op.drop_column("export_logs", "artifact_scheme")
    op.drop_column("export_logs", "artifact_nonce")
    op.drop_column("export_logs", "artifact_ciphertext")
    op.drop_column("export_logs", "artifact_size_bytes")
    op.drop_column("export_logs", "checksum_sha256")
    op.drop_column("export_logs", "manifest_version")
    op.drop_column("export_logs", "code_batch_id")

    op.drop_index("ix_code_batches_export_manifest_id", table_name="code_batches")
    op.drop_column("code_batches", "delivery_recipient")
    op.drop_column("code_batches", "delivered_at")
    op.drop_column("code_batches", "printing_at")
    op.drop_column("code_batches", "exported_at")
    op.drop_column("code_batches", "export_manifest_id")
    op.drop_column("code_batches", "expected_item_count")
    op.drop_column("code_batches", "source")
    op.drop_column("code_batches", "contract_version")
