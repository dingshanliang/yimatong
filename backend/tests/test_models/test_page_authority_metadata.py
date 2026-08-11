"""SQLite metadata smoke for partial page authority indexes."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.exc import IntegrityError

from app.models.page import PageTemplate, PageVersion
from app.models.product import Product  # noqa: F401 - resolves composite FK metadata
from app.models.tenant import Account  # noqa: F401 - resolves composite FK metadata


def test_sqlite_partial_page_uniqueness_preserves_history() -> None:
    engine = create_engine("sqlite://")
    PageTemplate.__table__.create(engine)
    PageVersion.__table__.create(engine)
    tenant_id = uuid.uuid4()
    product_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    active_template_id = uuid.uuid4()

    with engine.begin() as connection:
        for status in ("archived", "archived", "active"):
            template_id = active_template_id if status == "active" else uuid.uuid4()
            connection.execute(
                insert(PageTemplate).values(
                    id=template_id,
                    tenant_id=tenant_id,
                    product_id=product_id,
                    name=f"template-{template_id}",
                    template_type="traceability",
                    status=status,
                )
            )
        for number, status in enumerate(("draft", "draft", "archived", "archived", "published"), start=1):
            connection.execute(
                insert(PageVersion).values(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    page_template_id=active_template_id,
                    version=number,
                    config_json={},
                    status=status,
                    created_by=actor_id,
                    created_by_tenant_id=tenant_id,
                    published_at=datetime.now(UTC) if status == "published" else None,
                )
            )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(PageTemplate).values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                product_id=product_id,
                name="second active",
                template_type="traceability",
                status="active",
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(PageVersion).values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                page_template_id=active_template_id,
                version=6,
                config_json={},
                status="published",
                created_by=actor_id,
                created_by_tenant_id=tenant_id,
                published_at=datetime.now(UTC),
            )
        )
