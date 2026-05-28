# AES-GCM 手机号加密 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 AES-256-GCM 加密存储消费者手机号 + HMAC-SHA256 哈希索引，支持 kid 密钥轮换，预留 KMS 接口。

**Architecture:** 新建 `utils/crypto.py` 统一加密层，KeyProvider 接口抽象密钥来源（阶段一 EnvKeyProvider），ConsumerProfile 新增 `phone_encrypted` 字段，service/api 层改为传入明文手机号由底层自动加密。

**Tech Stack:** Python `cryptography` 库（AES-256-GCM）、HMAC-SHA256、SQLAlchemy、Alembic、pytest

**Design Spec:** `docs/superpowers/specs/2026-05-28-aes-gcm-encryption-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/app/utils/crypto.py` | Create | AES-GCM 加解密、HMAC-SHA256 哈希、KeyProvider 接口、mask_phone |
| `backend/app/core/config.py` | Modify | 新增 aes_master_key_v1/v2、hmac_pepper 配置项 |
| `backend/app/main.py` | Modify | lifespan 初始化 crypto |
| `backend/app/models/member.py` | Modify | ConsumerProfile 新增 phone_encrypted 字段 |
| `backend/app/services/member.py` | Modify | get_or_create_consumer 改为接收明文 phone |
| `backend/app/api/v1/members.py` | Modify | API 接收 phone 明文替代 phone_hash |
| `backend/app/services/gmv.py` | Modify | match_order 改用 hash_phone() |
| `backend/app/api/v1/gmv.py` | Modify | 响应中 phone_hash 脱敏 |
| `backend/alembic/versions/0003_phone_encrypted.py` | Create | 新增 phone_encrypted 列 |
| `backend/tests/unit/test_crypto.py` | Create | 加密模块单元测试 |
| `backend/tests/test_api/test_member.py` | Modify | 更新测试用例 |
| `backend/tests/test_api/test_gmv.py` | Modify | 更新测试用例 |

---

### Task 1: 加密核心模块 — 测试先行

**Files:**
- Create: `backend/tests/unit/test_crypto.py`
- Create: `backend/app/utils/crypto.py`

- [ ] **Step 1: 创建测试文件和目录**

```bash
mkdir -p backend/tests/unit
touch backend/tests/unit/__init__.py
```

- [ ] **Step 2: 编写加密模块的失败测试**

Create `backend/tests/unit/test_crypto.py`:

```python
"""AES-GCM 加密 + HMAC-SHA256 哈希 单元测试"""

import os

import pytest


# 在导入 crypto 之前设置测试密钥
os.environ["AES_MASTER_KEY_V1"] = "00" * 32
os.environ["HMAC_PEPPER"] = "ff" * 32

from app.utils.crypto import (
    CryptoError,
    decrypt_phone,
    encrypt_phone,
    hash_phone,
    init_crypto,
    mask_phone,
)
from app.utils.crypto import EnvKeyProvider


@pytest.fixture(autouse=True)
def _init_crypto():
    """每个测试前初始化加密模块"""
    init_crypto(EnvKeyProvider())


class TestEncryptDecrypt:
    """AES-256-GCM 加解密"""

    def test_roundtrip(self):
        """加密后解密应还原明文"""
        phone = "13800138000"
        encrypted = encrypt_phone(phone)
        assert decrypt_phone(encrypted) == phone

    def test_unique_ciphertext(self):
        """相同明文两次加密应产生不同密文（不同 iv）"""
        phone = "13900139000"
        e1 = encrypt_phone(phone)
        e2 = encrypt_phone(phone)
        assert e1 != e2
        assert decrypt_phone(e1) == phone
        assert decrypt_phone(e2) == phone

    def test_decrypt_tampered_ciphertext(self):
        """篡改密文应抛出异常"""
        encrypted = encrypt_phone("13800138000")
        tampered = encrypted[:-4] + "XXXX"
        with pytest.raises(CryptoError):
            decrypt_phone(tampered)

    def test_decrypt_invalid_base64(self):
        """无效 Base64 应抛出异常"""
        with pytest.raises(CryptoError):
            decrypt_phone("not-valid-base64!!!")

    def test_decrypt_unknown_kid(self):
        """未知 kid 应抛出异常"""
        import base64
        import struct

        # 构造 kid=999 的密文
        fake_data = struct.pack(">H", 999) + b"\x00" * 28
        fake_b64 = base64.b64encode(fake_data).decode()
        with pytest.raises(CryptoError, match="key"):
            decrypt_phone(fake_b64)

    def test_empty_phone(self):
        """空字符串应正常加解密"""
        encrypted = encrypt_phone("")
        assert decrypt_phone(encrypted) == ""


class TestHmacHash:
    """HMAC-SHA256 哈希索引"""

    def test_deterministic(self):
        """相同手机号应产生相同 hash"""
        h1 = hash_phone("13800138000")
        h2 = hash_phone("13800138000")
        assert h1 == h2

    def test_different_phones(self):
        """不同手机号应产生不同 hash"""
        h1 = hash_phone("13800138000")
        h2 = hash_phone("13900139000")
        assert h1 != h2

    def test_hex_length(self):
        """hash 应为 64 字符 hex 字符串"""
        h = hash_phone("13800138000")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestMaskPhone:
    """手机号脱敏"""

    def test_11_digit(self):
        assert mask_phone("13800138000") == "138****8000"

    def test_short_number(self):
        assert mask_phone("12345") == "****"

    def test_empty(self):
        assert mask_phone("") == ""


class TestKeyProvider:
    """EnvKeyProvider"""

    def test_load_keys(self):
        provider = EnvKeyProvider()
        assert provider.get_current_kid() == 1
        key = provider.get_key(1)
        assert len(key) == 32

    def test_unknown_kid(self):
        provider = EnvKeyProvider()
        with pytest.raises(KeyError):
            provider.get_key(999)

    def test_pepper(self):
        provider = EnvKeyProvider()
        pepper = provider.get_pepper()
        assert len(pepper) == 32
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/unit/test_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.utils.crypto'`

- [ ] **Step 4: 实现加密核心模块**

Create `backend/app/utils/crypto.py`:

```python
"""AES-256-GCM 加密 + HMAC-SHA256 哈希索引

密文格式: Base64(kid(2B) || iv(12B) || ciphertext(NB) || tag(16B))
HMAC 索引: HMAC-SHA256(plaintext, pepper) → 64 hex chars
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CryptoError(Exception):
    """加密/解密错误"""


class KeyProvider(Protocol):
    def get_current_kid(self) -> int: ...
    def get_key(self, kid: int) -> bytes: ...
    def get_pepper(self) -> bytes: ...


class EnvKeyProvider:
    """从环境变量读取密钥"""

    def __init__(self):
        self._keys: dict[int, bytes] = {}
        self._current_kid: int = 1
        self._pepper: bytes = b""
        self._load()

    def _load(self):
        kid = 1
        while True:
            key_hex = os.environ.get(f"AES_MASTER_KEY_V{kid}", "")
            if not key_hex:
                break
            self._keys[kid] = bytes.fromhex(key_hex)
            self._current_kid = kid
            kid += 1

        if not self._keys:
            raise CryptoError("No AES_MASTER_KEY_Vn configured")

        pepper_hex = os.environ.get("HMAC_PEPPER", "")
        if not pepper_hex:
            raise CryptoError("HMAC_PEPPER not configured")
        self._pepper = bytes.fromhex(pepper_hex)

    def get_current_kid(self) -> int:
        return self._current_kid

    def get_key(self, kid: int) -> bytes:
        if kid not in self._keys:
            raise KeyError(f"Unknown key id: {kid}")
        return self._keys[kid]

    def get_pepper(self) -> bytes:
        return self._pepper


_provider: KeyProvider | None = None


def init_crypto(provider: KeyProvider):
    global _provider
    _provider = provider


def _get_provider() -> KeyProvider:
    if _provider is None:
        raise CryptoError("Crypto not initialized. Call init_crypto() first.")
    return _provider


def encrypt_phone(plaintext: str) -> str:
    """加密手机号，返回 Base64 编码密文"""
    provider = _get_provider()
    kid = provider.get_current_kid()
    key = provider.get_key(kid)

    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext_with_tag = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    # AESGCM.encrypt 返回 ciphertext || tag (后16字节为 tag)
    ciphertext = ciphertext_with_tag[:-16]
    tag = ciphertext_with_tag[-16:]

    raw = struct.pack(">H", kid) + iv + ciphertext + tag
    return base64.b64encode(raw).decode("ascii")


def decrypt_phone(ciphertext_b64: str) -> str:
    """解密手机号"""
    provider = _get_provider()
    try:
        raw = base64.b64decode(ciphertext_b64)
    except Exception as e:
        raise CryptoError(f"Invalid base64: {e}") from e

    if len(raw) < 2 + 12 + 16:
        raise CryptoError("Ciphertext too short")

    kid = struct.unpack(">H", raw[:2])[0]
    iv = raw[2:14]
    ciphertext = raw[14:-16]
    tag = raw[-16:]

    try:
        key = provider.get_key(kid)
    except KeyError as e:
        raise CryptoError(f"Unknown key id {kid}") from e

    aesgcm = AESGCM(key)
    ciphertext_with_tag = ciphertext + tag
    try:
        plaintext = aesgcm.decrypt(iv, ciphertext_with_tag, None)
    except Exception as e:
        raise CryptoError(f"Decryption failed: {e}") from e

    return plaintext.decode("utf-8")


def hash_phone(phone: str) -> str:
    """HMAC-SHA256 哈希，返回 64 字符 hex"""
    provider = _get_provider()
    return hmac.new(
        provider.get_pepper(), phone.encode("utf-8"), hashlib.sha256,
    ).hexdigest()


def mask_phone(phone: str) -> str:
    """手机号脱敏：显示前3后4"""
    if len(phone) == 11:
        return phone[:3] + "****" + phone[7:]
    return "****" if phone else ""
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/unit/test_crypto.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add backend/app/utils/crypto.py backend/tests/unit/__init__.py backend/tests/unit/test_crypto.py
git commit -m "feat(crypto): AES-256-GCM encrypt + HMAC-SHA256 hash module"
```

---

### Task 2: 配置层 + 应用初始化

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: 修改 config.py 新增加密配置**

在 `backend/app/core/config.py` 的 `Settings` 类中新增字段：

```python
    # AES-256-GCM 加密密钥（hex 编码，按版本号）
    aes_master_key_v1: str = ""  # 阶段一主密钥
    aes_master_key_v2: str = ""  # 可选，密钥轮换时使用

    # HMAC-SHA256 pepper（hex 编码）
    hmac_pepper: str = ""
```

完整文件变为：

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://yimatong:yimatong@localhost:5432/yimatong_dev"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-key-change-in-production"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # AES-256-GCM 加密密钥（hex 编码，按版本号）
    aes_master_key_v1: str = ""  # 阶段一主密钥
    aes_master_key_v2: str = ""  # 可选，密钥轮换时使用

    # HMAC-SHA256 pepper（hex 编码）
    hmac_pepper: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
```

- [ ] **Step 2: 修改 main.py 添加 lifespan 初始化**

将 `backend/app/main.py` 的 `app` 创建替换为带 lifespan 的版本。在文件顶部 imports 区添加：

```python
from contextlib import asynccontextmanager
```

在 `app = FastAPI(...)` 行之前添加 lifespan 函数：

```python
@asynccontextmanager
async def lifespan(app):
    # 初始化加密模块（仅当配置了密钥时）
    from app.utils.crypto import EnvKeyProvider, init_crypto

    if settings.aes_master_key_v1 and settings.hmac_pepper:
        init_crypto(EnvKeyProvider())

    yield
```

将 `app = FastAPI(title="一码通", version="0.1.0")` 改为：

```python
app = FastAPI(title="一码通", version="0.1.0", lifespan=lifespan)
```

在文件顶部 imports 区添加：

```python
from app.core.config import settings
```

- [ ] **Step 3: 修改 conftest.py 注入测试密钥**

在 `backend/tests/conftest.py` 的环境变量设置区（第 13-15 行之后）添加测试加密密钥：

```python
os.environ.setdefault("aes_master_key_v1", "00" * 32)
os.environ.setdefault("hmac_pepper", "ff" * 32)
```

- [ ] **Step 4: 运行现有测试确认不破坏**

Run: `cd backend && python -m pytest tests/test_api/test_member.py tests/test_api/test_gmv.py -v`
Expected: 全部 PASS（加密模块已初始化但尚未被调用）

- [ ] **Step 5: 提交**

```bash
git add backend/app/core/config.py backend/app/main.py backend/tests/conftest.py
git commit -m "feat(config): add AES/GCM key config and crypto lifespan init"
```

---

### Task 3: ConsumerProfile 模型变更

**Files:**
- Modify: `backend/app/models/member.py`

- [ ] **Step 1: 新增 phone_encrypted 字段**

在 `backend/app/models/member.py` 的 `ConsumerProfile` 类中，在 `phone_hash` 行之后添加：

```python
from sqlalchemy import Text
```

在 imports 区添加 `Text`（已有 `String`，在其后添加）。

在 `phone_hash` 字段之后添加：

```python
    phone_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: 运行测试确认模型变更不破坏**

Run: `cd backend && python -m pytest tests/test_api/test_member.py -v`
Expected: PASS（SQLite 自动创建新列）

- [ ] **Step 3: 提交**

```bash
git add backend/app/models/member.py
git commit -m "feat(model): add phone_encrypted field to ConsumerProfile"
```

---

### Task 4: Alembic 迁移

**Files:**
- Create: `backend/alembic/versions/0003_phone_encrypted.py`

- [ ] **Step 1: 创建迁移文件**

Create `backend/alembic/versions/0003_phone_encrypted.py`:

```python
"""add phone_encrypted to consumer_profiles

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "consumer_profiles",
        sa.Column("phone_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("consumer_profiles", "phone_encrypted")
```

- [ ] **Step 2: 提交**

```bash
git add backend/alembic/versions/0003_phone_encrypted.py
git commit -m "feat(migration): add phone_encrypted column to consumer_profiles"
```

---

### Task 5: 服务层改造 — member.py

**Files:**
- Modify: `backend/app/services/member.py`

- [ ] **Step 1: 改造 get_or_create_consumer**

将 `backend/app/services/member.py` 的 `get_or_create_consumer` 函数替换为：

```python
async def get_or_create_consumer(
    db: AsyncSession, tenant_id: uuid.UUID, phone: str | None = None,
) -> ConsumerProfile:
    """获取或创建消费者档案"""
    if phone:
        phone_h = hash_phone(phone)
        result = await db.execute(
            select(ConsumerProfile).where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.phone_hash == phone_h,
            )
        )
        consumer = result.scalar_one_or_none()
        if consumer:
            return consumer

    consumer = ConsumerProfile(
        tenant_id=tenant_id,
        phone_hash=hash_phone(phone) if phone else None,
        phone_encrypted=encrypt_phone(phone) if phone else None,
    )
    db.add(consumer)
    await db.commit()
    await db.refresh(consumer)
    return consumer
```

在文件顶部 imports 区添加：

```python
from app.utils.crypto import encrypt_phone, hash_phone
```

- [ ] **Step 2: 添加 get_consumer_phone 辅助函数**

在 `member.py` 文件末尾添加：

```python
async def get_consumer_phone(
    db: AsyncSession, consumer_id: uuid.UUID,
) -> str | None:
    """获取消费者脱敏手机号"""
    from app.utils.crypto import decrypt_phone, mask_phone

    result = await db.execute(
        select(ConsumerProfile).where(ConsumerProfile.id == consumer_id)
    )
    consumer = result.scalar_one_or_none()
    if not consumer or not consumer.phone_encrypted:
        return None
    try:
        return mask_phone(decrypt_phone(consumer.phone_encrypted))
    except Exception:
        return None
```

- [ ] **Step 3: 运行测试确认编译通过**

Run: `cd backend && python -m pytest tests/test_api/test_member.py -v`
Expected: 可能 FAIL（API 层还没改参数名），确认是预期的参数不匹配错误

- [ ] **Step 4: 提交**

```bash
git add backend/app/services/member.py
git commit -m "feat(member): service layer uses crypto for phone encryption"
```

---

### Task 6: API 层改造 — members.py

**Files:**
- Modify: `backend/app/api/v1/members.py`

- [ ] **Step 1: 修改 API 接收 phone 明文**

在 `backend/app/api/v1/members.py` 中：

将 `ConsumerCreateRequest` 的 `phone_hash` 改为 `phone`：

```python
class ConsumerCreateRequest(BaseModel):
    phone: str | None = None
    nickname: str | None = None
```

将 `create_consumer_endpoint` 中的 `phone_hash=body.phone_hash` 改为 `phone=body.phone`：

```python
    consumer = await get_or_create_consumer(db, tenant_id, phone=body.phone)
```

- [ ] **Step 2: 运行 member 测试确认（需要更新测试）**

暂不运行，等 Task 7 更新测试后一起运行。

- [ ] **Step 3: 提交**

```bash
git add backend/app/api/v1/members.py
git commit -m "feat(members-api): accept plaintext phone instead of phone_hash"
```

---

### Task 7: 更新 member 测试

**Files:**
- Modify: `backend/tests/test_api/test_member.py`

- [ ] **Step 1: 更新测试用例**

在 `backend/tests/test_api/test_member.py` 中，所有 `json={"phone_hash": ...}` 改为 `json={"phone": ...}`:

1. `TestConsumerProfile.test_create_consumer` 中:
   `"phone_hash": "abc123"` → `"phone": "13800138000"`

2. `TestMatching.test_match_by_phone`（在 test_gmv.py 中，Task 8 处理）

不需要修改其他无 phone 参数的测试用例。

- [ ] **Step 2: 运行 member 测试**

Run: `cd backend && python -m pytest tests/test_api/test_member.py -v`
Expected: 全部 PASS

- [ ] **Step 3: 提交**

```bash
git add backend/tests/test_api/test_member.py
git commit -m "test(member): update tests to use plaintext phone"
```

---

### Task 8: GMV 归因服务改造

**Files:**
- Modify: `backend/app/services/gmv.py`
- Modify: `backend/app/api/v1/gmv.py`
- Modify: `backend/tests/test_api/test_gmv.py`

- [ ] **Step 1: 修改 gmv service 的 match_order**

在 `backend/app/services/gmv.py` 中：

在文件顶部 imports 区添加：

```python
from app.utils.crypto import hash_phone
```

将 `match_order` 函数中 `match_by == "phone_hash"` 分支的逻辑改为：

```python
    if match_by == "phone_hash":
        # value 是明文手机号，hash 后匹配
        phone_h = hash_phone(value)
        consumer_result = await db.execute(
            select(ConsumerProfile).where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.phone_hash == phone_h,
            )
        )
        consumer = consumer_result.scalar_one_or_none()
        if consumer:
            order_result = await db.execute(
                select(ExternalOrder).where(
                    ExternalOrder.tenant_id == tenant_id,
                    ExternalOrder.phone_hash == phone_h,
                    ExternalOrder.matched.is_(False),
                )
            )
            for order in order_result.scalars().all():
                order.matched = True
                attr = GmvAttribution(
                    tenant_id=tenant_id,
                    external_order_id=order.id,
                    amount=order.amount,
                    match_type="phone_hash",
                )
                db.add(attr)
            await db.commit()
            return {"matched": True, "consumer_id": str(consumer.id)}
```

注意：`ExternalOrder.phone_hash` 的数据来源是外部渠道导入，导入时也需要 hash。修改 `import_orders` 函数中创建 ExternalOrder 的部分：

```python
        order = ExternalOrder(
            tenant_id=tenant_id,
            external_id=o["external_id"],
            amount=o["amount"],
            phone_hash=hash_phone(o["phone_hash"]) if o.get("phone_hash") else None,
            product_name=o.get("product_name"),
            order_time=datetime.fromisoformat(o["order_time"].replace("Z", "+00:00")) if o.get("order_time") else None,
        )
```

- [ ] **Step 2: 修改 gmv API 响应脱敏**

在 `backend/app/api/v1/gmv.py` 中：

`ExternalOrder.phone_hash` 现在存储 HMAC hash（不可逆），API 响应保持原样返回 hash 值用于匹配调试。无需修改 `list_orders_endpoint`。

- [ ] **Step 3: 更新 gmv 测试**

在 `backend/tests/test_api/test_gmv.py` 中：

将所有传入明文 phone_hash 的地方改为传入明文手机号（service 会自动 hash）：

1. `TestExternalOrderImport.test_import_orders` 中 `"phone_hash": "abc123"` — 这些是外部订单数据，保持不变（导入时 service 会自动 hash）

2. `TestMatching.test_match_by_phone` 中：
   - `"phone_hash": "match_phone"` → `"phone_hash": "13800138000"`
   - 创建消费者: `json={"phone_hash": "match_phone"}` → `json={"phone": "13800138000"}`
   - 匹配请求: `json={"match_by": "phone_hash", "value": "match_phone"}` → `json={"match_by": "phone_hash", "value": "13800138000"}`

3. `TestGmvDashboard.test_gmv_summary` 中 `phone_hash="gmv_phone"` — 这是直接构造模型对象，需要改为 hash 后的值：

   在 imports 区添加：
   ```python
   from app.utils.crypto import hash_phone, init_crypto
   from app.utils.crypto import EnvKeyProvider
   ```

   在 fixture 或测试开头添加初始化：
   ```python
   init_crypto(EnvKeyProvider())
   ```

   将 `phone_hash="gmv_phone"` 改为 `phone_hash=hash_phone("13800138001")`

4. `TestTransactionRecord.test_attribution_record` 中 `phone_hash="attr_phone"` 同理改为 `phone_hash=hash_phone("13800138002")`

5. 在文件顶部环境变量设置区（conftest.py 已处理）确保测试密钥可用。

- [ ] **Step 4: 运行全部 gmv 测试**

Run: `cd backend && python -m pytest tests/test_api/test_gmv.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/gmv.py backend/app/api/v1/gmv.py backend/tests/test_api/test_gmv.py
git commit -m "feat(gmv): use hash_phone for phone matching in GMV attribution"
```

---

### Task 9: 端到端回归测试

**Files:**
- No new files

- [ ] **Step 1: 运行全量测试**

Run: `cd backend && python -m pytest tests/ -v --tb=short`
Expected: 全部 PASS

- [ ] **Step 2: 检查 grep 确认无遗留 phone_hash 明文传入**

Run: `grep -rn 'phone_hash=' backend/app/api/ backend/app/services/ --include="*.py"`
Expected: 模型字段定义和 DB 查询中的 `phone_hash` 是字段名引用（正常），不应有明文字符串赋值

- [ ] **Step 3: 确认无临时文件**

Run: `git status`
Expected: 无未跟踪的临时文件（test_*.py, debug_*.py 等）

- [ ] **Step 4: 最终提交（如有遗漏修复）**

```bash
git add -A
git commit -m "feat(crypto): AES-GCM phone encryption complete — all tests pass"
```
