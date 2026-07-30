"""
Test: Alembic migrations from 0001 to head on a clean PostgreSQL instance.

This test:
1. Creates a temporary clean database on the local PostgreSQL instance.
2. Runs `alembic upgrade head` against it.
3. Verifies the migration succeeds.
4. Compares the migrated schema with SQLAlchemy models (autogenerate diff).
5. Drops the temporary database.

Requires a running PostgreSQL instance accessible at localhost:5432
with superuser privileges to create/drop databases.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent

# PostgreSQL connection for administrative operations (create/drop DB)
# Uses local superuser because 'yimatong' role lacks CREATEDB privilege.
ADMIN_DB_URL = os.getenv("TEST_ADMIN_DB_URL", "postgresql://ericding:@localhost:5432/postgres")
# Database URL for the temporary test database
TEST_DB_NAME = "yimatong_migration_test"
TEST_DB_URL = os.getenv(
    "TEST_MIGRATION_DB_URL", f"postgresql+asyncpg://yimatong:yimatong@localhost:5432/{TEST_DB_NAME}"
)


def _psql(sql: str) -> subprocess.CompletedProcess:
    """Run a SQL command via psql."""
    return subprocess.run(
        ["psql", ADMIN_DB_URL, "-c", sql],
        capture_output=True,
        text=True,
    )


def _db_exists() -> bool:
    result = _psql(f"SELECT 1 FROM pg_database WHERE datname = '{TEST_DB_NAME}';")
    return "1" in result.stdout


def _test_db_psql(database_url: str, sql: str) -> subprocess.CompletedProcess:
    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return subprocess.run(
        ["psql", sync_url, "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def clean_test_db():
    """Create a clean test database before tests and drop it after."""
    # Drop if exists from previous failed runs
    if _db_exists():
        _psql(f"DROP DATABASE {TEST_DB_NAME} WITH (FORCE);")

    result = _psql(f"CREATE DATABASE {TEST_DB_NAME} OWNER yimatong;")
    if result.returncode != 0:
        pytest.skip(f"Cannot create test database: {result.stderr}")

    yield TEST_DB_URL

    # Teardown
    if _db_exists():
        _psql(f"DROP DATABASE {TEST_DB_NAME} WITH (FORCE);")


class TestMigrationOnCleanDB:
    def test_migrations_run_on_clean_db(self, clean_test_db):
        """Phase 1 test: alembic upgrade head should succeed on a clean DB."""
        env = os.environ.copy()
        env["database_url"] = clean_test_db
        env["migration_database_url"] = clean_test_db

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0, f"Alembic upgrade failed!\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    def test_migrated_schema_matches_models(self, clean_test_db):
        """Verify migrated schema matches SQLAlchemy models (no diff)."""
        env = os.environ.copy()
        env["database_url"] = clean_test_db
        env["migration_database_url"] = clean_test_db

        # First ensure migrations are applied
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"Migration setup failed: {result.stderr}"

        # Ask Alembic to compare metadata without creating a throwaway revision.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-x",
                "baseline_legacy_timestamp_nullability=true",
                "check",
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0, (
            "Schema mismatch detected! Migrated DB differs from SQLAlchemy models.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    def test_required_tables_and_rls_are_present(self, clean_test_db):
        """Guard metadata registration and database-level tenant isolation."""
        result = _test_db_psql(
            clean_test_db,
            """
            INSERT INTO tenant_invite_codes (
                id, code, tenant_type, max_uses, used_count, status, created_by_actor
            )
            VALUES (
                '20000000-0000-0000-0000-000000000001',
                'PLATFORM-ACTOR-PROBE',
                'brand',
                1,
                0,
                'active',
                'platform-admin'
            );

            SELECT
                to_regclass('public.tenant_invite_codes') IS NOT NULL AS invite_codes_exists,
                bool_and(c.relrowsecurity) AS rls_enabled,
                count(p.policyname) = 2 AS policies_exist
            FROM pg_class c
            LEFT JOIN pg_policies p
              ON p.schemaname = 'public'
             AND p.tablename = c.relname
             AND p.policyname = 'tenant_isolation'
            WHERE c.relname IN ('anonymous_visitors', 'tenant_health_metrics');
            """,
        )
        assert result.returncode == 0, result.stderr
        assert "t|t|t" in result.stdout.replace(" ", ""), result.stdout

    def test_new_rls_policies_block_cross_tenant_reads_and_writes(self, clean_test_db):
        """Exercise both policy USING and WITH CHECK as a non-owner role."""
        role_name = "yimatong_migration_rls_probe"
        tenant_a = "00000000-0000-0000-0000-000000000001"
        tenant_b = "00000000-0000-0000-0000-000000000002"

        _psql(f"DROP ROLE IF EXISTS {role_name};")
        created = _psql(f"CREATE ROLE {role_name} NOLOGIN;")
        if created.returncode != 0:
            pytest.skip(f"Cannot create RLS probe role: {created.stderr}")

        try:
            setup = _test_db_psql(
                clean_test_db,
                f"""
                INSERT INTO tenants (id, name, slug, status, plan, tenant_type)
                VALUES
                    ('{tenant_a}', 'RLS A', 'rls-a', 'active', 'free', 'brand'),
                    ('{tenant_b}', 'RLS B', 'rls-b', 'active', 'free', 'brand');
                INSERT INTO anonymous_visitors (id, tenant_id, visitor_id)
                VALUES
                    ('10000000-0000-0000-0000-000000000001', '{tenant_a}', 'visitor-a'),
                    ('10000000-0000-0000-0000-000000000002', '{tenant_b}', 'visitor-b');
                GRANT USAGE ON SCHEMA public TO {role_name};
                GRANT SELECT, INSERT ON anonymous_visitors TO {role_name};
                """,
            )
            assert setup.returncode == 0, setup.stderr

            probe = _test_db_psql(
                clean_test_db,
                f"""
                SET ROLE {role_name};
                SELECT set_config('app.tenant_id', '{tenant_a}', false);
                DO $$
                DECLARE visible_rows integer;
                BEGIN
                    SELECT count(*) INTO visible_rows FROM anonymous_visitors;
                    IF visible_rows <> 1 THEN
                        RAISE EXCEPTION 'cross-tenant read leaked % rows', visible_rows;
                    END IF;

                    BEGIN
                        INSERT INTO anonymous_visitors (id, tenant_id, visitor_id)
                        VALUES (
                            '10000000-0000-0000-0000-000000000003',
                            '{tenant_b}',
                            'visitor-cross-tenant'
                        );
                        RAISE EXCEPTION 'cross-tenant insert unexpectedly succeeded';
                    EXCEPTION
                        WHEN insufficient_privilege THEN NULL;
                    END;
                END
                $$;
                RESET ROLE;
                """,
            )
            assert probe.returncode == 0, f"RLS probe failed:\n{probe.stdout}\n{probe.stderr}"
        finally:
            _test_db_psql(clean_test_db, f"DROP OWNED BY {role_name};")
            _psql(f"DROP ROLE IF EXISTS {role_name};")
