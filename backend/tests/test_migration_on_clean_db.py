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

        # First ensure migrations are applied
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"Migration setup failed: {result.stderr}"

        # Run autogenerate to check for differences
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "revision", "--autogenerate", "-m", "test_diff"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            env=env,
        )

        # If autogenerate itself fails, that's a problem
        assert result.returncode == 0, (
            f"Alembic autogenerate failed!\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        # Check if a new revision file was created (indicating differences)
        versions_dir = BACKEND_DIR / "alembic" / "versions"
        diff_files = list(versions_dir.glob("*test_diff*.py"))

        if diff_files:
            diff_content = diff_files[0].read_text()
            # Clean up the diff file regardless
            diff_files[0].unlink()
            # Check if the upgrade function is empty (no actual changes)
            # Alembic generates empty upgrade/downgrade when no diff
            if "def upgrade()" in diff_content:
                # Extract the upgrade function body
                lines = diff_content.splitlines()
                in_upgrade = False
                upgrade_body = []
                for line in lines:
                    if line.startswith("def upgrade()"):
                        in_upgrade = True
                        continue
                    if in_upgrade:
                        if line and not line.startswith(" ") and not line.startswith("\t"):
                            break
                        upgrade_body.append(line)
                body_text = "\n".join(upgrade_body).strip()
                # If body is just pass or empty, no real diff
                if body_text and body_text != "pass":
                    # Filter out PostgreSQL partition-table noise.
                    # Partition child tables (e.g. scan_events_2026_05) are created
                    # via raw SQL in migrations and are not reflected in SQLAlchemy
                    # models, so autogenerate always reports them as "removed".
                    non_partition_lines = [
                        ln
                        for ln in upgrade_body
                        if ln.strip() and "scan_events_" not in ln and not ln.strip().startswith("#")
                    ]
                    if non_partition_lines:
                        pytest.fail(
                            f"Schema mismatch detected! Migrated DB differs from SQLAlchemy models.\n"
                            f"Autogenerate created a non-empty upgrade.\n"
                            f"Diff file content:\n{diff_content}"
                        )
