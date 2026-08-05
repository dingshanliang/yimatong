import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_create_account_rejects_duplicate_email():
    """Duplicate email within same tenant should be rejected with 409"""
    from app.services.organization import create_account

    db = AsyncMock()

    existing_account = MagicMock()
    existing_account.email = "dup@test.com"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_account

    mock_org_result = MagicMock()
    mock_org_result.scalar_one_or_none.return_value = MagicMock()

    call_count = 0

    async def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        stmt_str = str(stmt)
        if "email" in stmt_str.lower() and "account" in stmt_str.lower():
            return mock_result
        return mock_org_result

    db.execute = mock_execute

    with pytest.raises(ValueError, match="already exists|已存在"):
        await create_account(
            db=db,
            tenant_id=MagicMock(),
            organization_id=MagicMock(),
            email="dup@test.com",
            name="重复用户",
            password="Test12345678",
        )


@pytest.mark.asyncio
async def test_create_account_normalizes_email_before_duplicate_check():
    from app.services.organization import create_account

    db = AsyncMock()
    org_result = MagicMock()
    org_result.scalar_one_or_none.return_value = object()
    account_result = MagicMock()
    account_result.scalar_one_or_none.return_value = object()
    db.execute.side_effect = [org_result, account_result]

    with pytest.raises(ValueError, match="already exists"):
        await create_account(
            db=db,
            tenant_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            email="  MEMBER@EXAMPLE.COM ",
            name="Member",
            password="Password1",
        )

    account_query = db.execute.await_args_list[1].args[0]
    assert account_query.compile().params["email_1"] == "member@example.com"
