"""A1-006: JWT 双 token 认证体系验收测试"""


from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_access_token,
    verify_password,
    verify_refresh_token,
)


class TestPasswordHashing:
    def test_hash_and_verify(self):
        hashed = hash_password("secret123")
        assert hashed != "secret123"
        assert verify_password("secret123", hashed)

    def test_wrong_password_fails(self):
        hashed = hash_password("secret123")
        assert not verify_password("wrong", hashed)


class TestAccessToken:
    def test_create_and_decode(self):
        token = create_access_token(
            tenant_id="t-001",
            account_id="a-001",
            role="admin",
        )
        payload = decode_token(token)
        assert payload["sub"] == "a-001"
        assert payload["tenant_id"] == "t-001"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "jti" in payload

    async def test_verify_access_token_success(self):
        token = create_access_token("t-001", "a-001", "admin")
        payload = await verify_access_token(token)
        assert payload is not None
        assert payload["type"] == "access"

    async def test_verify_access_token_rejects_refresh(self):
        token = create_refresh_token("a-001")
        result = await verify_access_token(token)
        assert result is None

    def test_access_token_has_jti(self):
        token = create_access_token("t-001", "a-001", "admin")
        payload = decode_token(token)
        assert "jti" in payload
        assert len(payload["jti"]) > 0


class TestRefreshToken:
    def test_create_and_decode(self):
        token = create_refresh_token("a-001")
        payload = decode_token(token)
        assert payload["sub"] == "a-001"
        assert payload["type"] == "refresh"
        assert "jti" in payload

    async def test_verify_refresh_token_success(self):
        token = create_refresh_token("a-001")
        payload = await verify_refresh_token(token)
        assert payload is not None
        assert payload["type"] == "refresh"

    async def test_verify_refresh_token_rejects_access(self):
        token = create_access_token("t-001", "a-001", "admin")
        result = await verify_refresh_token(token)
        assert result is None

    def test_refresh_token_has_jti(self):
        token = create_refresh_token("a-001")
        payload = decode_token(token)
        assert "jti" in payload


class TestTokenSecurity:
    async def test_tampered_token_fails(self):
        token = create_access_token("t-001", "a-001", "admin")
        tampered = token[:-5] + "xxxxx"
        result = await verify_access_token(tampered)
        assert result is None

    async def test_invalid_token_fails(self):
        result = await verify_access_token("not-a-token")
        assert result is None
