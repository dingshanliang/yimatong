import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException


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

    with pytest.raises(HTTPException) as exc_info:
        await create_account(
            db=db,
            tenant_id=MagicMock(),
            organization_id=MagicMock(),
            email="dup@test.com",
            name="重复用户",
            password="Test12345678",
        )
    assert exc_info.value.status_code == 409
    assert "already exists" in exc_info.value.detail.lower() or "已存在" in exc_info.value.detail
