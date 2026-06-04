from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_update_tenant_merges_compliance_settings():
    """compliance_settings should merge, not full replace"""
    from app.services.tenant import update_tenant

    mock_tenant = MagicMock()
    mock_tenant.compliance_settings = {
        "privacy_version": "1.0",
        "phone_auth": True,
    }

    db = AsyncMock()
    with patch("app.services.tenant.get_tenant", return_value=mock_tenant):
        await update_tenant(
            db,
            tenant_id=MagicMock(),
            compliance_settings={"privacy_version": "2.0", "privacy_content": "new"},
        )

    merged = mock_tenant.compliance_settings
    assert merged["phone_auth"] is True, "existing fields should be preserved"
    assert merged["privacy_version"] == "2.0", "new values should overwrite"
    assert merged["privacy_content"] == "new", "new fields should be added"


@pytest.mark.asyncio
async def test_update_tenant_compliance_settings_none_skips():
    """Passing None should not modify compliance_settings"""
    from app.services.tenant import update_tenant

    mock_tenant = MagicMock()
    original_settings = {"privacy_version": "1.0"}
    mock_tenant.compliance_settings = original_settings

    db = AsyncMock()
    with patch("app.services.tenant.get_tenant", return_value=mock_tenant):
        await update_tenant(db, tenant_id=MagicMock(), compliance_settings=None)

    assert mock_tenant.compliance_settings == original_settings
