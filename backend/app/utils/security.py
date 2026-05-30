import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(tenant_id: str, account_id: str, role: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(account_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "exp": expire,
        "type": "access",
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token(account_id: str) -> str:
    expire = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    jti = str(uuid.uuid4())
    payload = {
        "sub": str(account_id),
        "exp": expire,
        "type": "refresh",
        "jti": jti,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])


def verify_access_token(token: str) -> dict | None:
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        # Check token revocation
        jti = payload.get("jti")
        if jti:
            from app.services.redis_cache import RedisCache

            cache = RedisCache()
            if cache.is_token_revoked(jti):
                return None
        return payload
    except JWTError:
        return None


def verify_refresh_token(token: str) -> dict | None:
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError:
        return None
