# AES-GCM 手机号加密 + 安全加固 设计文档

> **Status:** ✅ Completed — fully implemented in `backend/app/utils/crypto.py`

日期：2026-05-28
状态：待评审
关联 PRD：关键设计约束 — 手机号 AES-GCM 加密 + HMAC-SHA256 哈希索引

## 1. 背景与目标

### 当前状态

- `ConsumerProfile.phone_hash`：bcrypt hash（String(64)），仅用于查重，不可解密
- 无 AES-GCM 加密基础设施
- 无密钥管理体系

### PRD 要求

> 手机号：AES-GCM 加密存储 + HMAC-SHA256 加盐哈希索引（服务端 pepper，非每用户独立盐——独立盐会阻碍按手机号查重/匹配）
> 密钥管理：阶段一用环境变量注入；预留 KMS envelope encryption 接口（kid 标识每个密钥版本）
> 安全工具层：`utils/crypto.py` 统一处理

### 目标

1. 创建 `utils/crypto.py` — AES-256-GCM 加密/解密 + HMAC-SHA256 哈希索引
2. 支持 kid 密钥版本，支持密钥轮换
3. KeyProvider 接口预留 KMS 扩展
4. 修改 ConsumerProfile 模型：新增 `phone_encrypted` 字段，改造 `phone_hash` 为 HMAC-SHA256
5. 更新所有使用 phone_hash 的 service/api 层代码
6. 补全 Alembic 迁移
7. 补全单元测试

## 2. 设计决策

| 决策项 | 选择 | 原因 |
|--------|------|------|
| 加密范围 | 仅消费者手机号 | PRD 核心要求，用户确认最小范围 |
| 加密算法 | AES-256-GCM | PRD 明确要求 |
| 索引算法 | HMAC-SHA256（全局 pepper） | PRD 要求，支持查重匹配 |
| 密钥管理 | 环境变量 + KeyProvider 接口预留 KMS | 阶段一方案 |
| 密钥轮换 | 支持（kid 密钥版本） | PRD 要求 |
| 旧数据迁移 | 不迁移 | 项目开发阶段，无生产数据 |
| 加密库 | Python `cryptography` | 行业标准，无额外依赖 |

## 3. 架构设计

### 3.1 密文格式

```
Base64( kid(2B) || iv(12B) || ciphertext(NB) || tag(16B) )
```

- `kid`：2 字节 uint16 big-endian，标识加密使用的密钥版本
- `iv`：12 字节随机 nonce（每次加密独立生成，CSPRNG）
- `ciphertext`：AES-256-GCM 加密后的明文
- `tag`：16 字节 GCM 认证标签

示例密文（手机号 13800138000）：

```
AAEC9Jz+3kVrT5mN...（约 80-100 字符 Base64）
```

### 3.2 HMAC-SHA256 索引

```
phone_hash = HMAC-SHA256(phone_number, pepper)
```

- `pepper`：服务端全局密钥，存储在环境变量 `HMAC_PEPPER`
- 输出：64 字符 hex 字符串（SHA-256 摘要长度）
- 相同手机号 + 相同 pepper → 相同 hash → 支持查重匹配
- pepper 不可轮换（轮换需要重新计算所有 hash）

### 3.3 KeyProvider 接口

```python
from typing import Protocol

class KeyProvider(Protocol):
    """密钥提供者接口，支持环境变量和未来 KMS 扩展"""

    def get_current_kid(self) -> int:
        """返回当前活跃密钥版本号"""
        ...

    def get_key(self, kid: int) -> bytes:
        """根据 kid 返回 256-bit AES 密钥"""
        ...

    def get_pepper(self) -> bytes:
        """返回 HMAC pepper"""
        ...
```

### 3.4 阶段一实现：EnvKeyProvider

```python
class EnvKeyProvider:
    """从环境变量读取密钥

    环境变量格式：
    - AES_MASTER_KEY_V1=hex编码的256bit密钥
    - AES_MASTER_KEY_V2=hex编码的256bit密钥（可选，轮换时添加）
    - HMAC_PEPPER=hex编码的256bit pepper
    """

    def __init__(self):
        self._keys: dict[int, bytes] = {}
        self._current_kid: int = 1
        self._load_keys()

    def _load_keys(self):
        # 读取 AES_MASTER_KEY_V1, V2, ...
        # 读取 HMAC_PEPPER
        ...

    def get_current_kid(self) -> int:
        return self._current_kid

    def get_key(self, kid: int) -> bytes:
        if kid not in self._keys:
            raise KeyError(f"Unknown key id: {kid}")
        return self._keys[kid]

    def get_pepper(self) -> bytes:
        return self._pepper
```

### 3.5 核心加密 API

```python
# utils/crypto.py

# 全局单例
_provider: KeyProvider | None = None

def init_crypto(provider: KeyProvider):
    """初始化加密模块（应用启动时调用一次）"""
    global _provider
    _provider = provider

def encrypt_phone(phone: str) -> str:
    """加密手机号，返回 Base64 编码密文"""
    ...

def decrypt_phone(ciphertext_b64: str) -> str:
    """解密手机号，根据密文中的 kid 选择密钥"""
    ...

def hash_phone(phone: str) -> str:
    """HMAC-SHA256 哈希，返回 64 字符 hex"""
    ...
```

## 4. 数据模型变更

### 4.1 ConsumerProfile 变更

**现有字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| phone_hash | String(64) | bcrypt hash |

**变更后**：

| 字段 | 类型 | 说明 |
|------|------|------|
| phone_hash | String(64) | HMAC-SHA256 哈希索引（替换 bcrypt） |
| phone_encrypted | Text | AES-GCM 加密密文（新增） |

- `phone_hash`：保持字段名和长度不变，但语义从 bcrypt hash 变为 HMAC-SHA256 hash
- `phone_encrypted`：新增字段，存储 AES-GCM 加密后的手机号密文
- `phone_hash` 上的 unique 约束保留，用于查重

### 4.2 其他模型

本次不修改其他模型（仅消费者手机号范围）。

## 5. 服务层变更

### 5.1 member.py — get_or_create_consumer

**变更前**（调用方传入 phone_hash）：

```python
async def get_or_create_consumer(db, tenant_id, phone_hash=None):
    # 用 phone_hash 查重
    # 创建时直接存 phone_hash
```

**变更后**（调用方传入明文手机号，service 层内部处理加密）：

```python
async def get_or_create_consumer(db, tenant_id, phone: str | None = None):
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
    ...
```

### 5.2 API 层变更

调用 `get_or_create_consumer` 的地方，从传入 `phone_hash` 改为传入明文 `phone`。

涉及文件（已确认调用点）：

| 文件 | 当前用法 | 变更 |
|------|---------|------|
| `api/v1/members.py:25,61` | API 接收 `phone_hash` 字段 | 改为接收 `phone` 明文字段 |
| `api/v1/gmv.py:25,71` | ExternalOrder 的 `phone_hash` 查询/返回 | 查询改用 `hash_phone()`，响应脱敏 |
| `services/gmv.py:23,59-72` | GMV 归因匹配用 `phone_hash` | 调用方传入明文手机号，service 内部 `hash_phone()` 后匹配 |

### 5.3 GMV 归因服务特殊处理

`ExternalOrder` 模型的 `phone_hash` 字段（来自外部渠道数据）不在本次加密范围内，但 GMV 归因匹配需要：
- 外部订单的 `phone_hash` 保持不变（渠道侧数据）
- 消费者匹配时，需要将明文手机号 hash 后与 `ExternalOrder.phone_hash` 比较
- 如果外部渠道也接入加密，未来可通过 `hash_phone()` 统一处理

### 5.4 响应中手机号脱敏

API 响应中需要返回手机号时，解密后脱敏显示：`138****8000`。

```python
def mask_phone(phone: str) -> str:
    """手机号脱敏：显示前3后4"""
    if len(phone) == 11:
        return phone[:3] + "****" + phone[7:]
    return "****"
```

## 6. 配置变更

### 6.1 环境变量

新增：

```env
# AES-256-GCM 主密钥（hex 编码，32 字节 = 64 hex 字符）
AES_MASTER_KEY_V1=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

# HMAC-SHA256 pepper（hex 编码，32 字节 = 64 hex 字符）
HMAC_PEPPER=abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

### 6.2 config.py 变更

```python
class Settings(BaseSettings):
    # ... 现有字段 ...

    # AES 加密密钥（按版本号）
    aes_master_key_v1: str = ""  # hex 编码
    aes_master_key_v2: str = ""  # 可选，密钥轮换时使用

    # HMAC pepper
    hmac_pepper: str = ""  # hex 编码
```

### 6.3 应用初始化

在 `main.py` 的 lifespan 中初始化加密模块：

```python
from app.utils.crypto import init_crypto, EnvKeyProvider

@asynccontextmanager
async def lifespan(app):
    init_crypto(EnvKeyProvider())
    yield
```

## 7. 密钥轮换流程

1. 生成新密钥，添加环境变量 `AES_MASTER_KEY_V2`
2. 重启应用，新加密使用 V2 密钥
3. 旧密文仍按 kid=1 解密（双版本共存）
4. 可选：后台任务逐步重新加密旧数据
5. 所有数据迁移到 V2 后，移除 `AES_MASTER_KEY_V1`

## 8. 测试策略

### 8.1 单元测试（utils/crypto.py）

| 测试用例 | 说明 |
|---------|------|
| test_encrypt_decrypt_roundtrip | 加密后解密应还原明文 |
| test_hash_deterministic | 相同手机号 → 相同 hash |
| test_hash_different_pepper | 不同 pepper → 不同 hash |
| test_encrypt_unique_ciphertext | 相同明文两次加密 → 不同密文（不同 iv） |
| test_decrypt_with_kid | 按 kid 选择正确密钥解密 |
| test_decrypt_unknown_kid | 未知 kid 应抛出 KeyError |
| test_key_provider_load | EnvKeyProvider 正确加载密钥 |
| test_invalid_ciphertext | 篡改密文应抛出异常 |
| test_mask_phone | 脱敏函数正确处理 11 位手机号 |

### 8.2 集成测试

| 测试用例 | 说明 |
|---------|------|
| test_consumer_create_with_phone | 创建消费者时 phone_encrypted 和 phone_hash 正确存储 |
| test_consumer_lookup_by_phone | 通过明文手机号查找消费者 |
| test_consumer_duplicate_phone | 相同手机号不创建重复消费者 |
| test_api_phone_not_in_response | API 响应中不返回加密密文 |

### 8.3 测试密钥

测试环境使用固定密钥，从 `conftest.py` 注入，不依赖环境变量：

```python
# tests/conftest.py
TEST_AES_KEY = bytes.fromhex("00" * 32)
TEST_HMAC_PEPPER = bytes.fromhex("ff" * 32)
```

## 9. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/utils/crypto.py` | 新建 | AES-GCM + HMAC-SHA256 + KeyProvider + mask_phone |
| `backend/app/core/config.py` | 修改 | 新增 aes_master_key_v1/v2、hmac_pepper 配置 |
| `backend/app/main.py` | 修改 | lifespan 中 init_crypto() |
| `backend/app/models/member.py` | 修改 | ConsumerProfile 新增 phone_encrypted 字段 |
| `backend/app/services/member.py` | 修改 | get_or_create_consumer 接收明文 phone，内部加密 |
| `backend/app/api/v1/members.py` | 修改 | API 接收 phone 明文替代 phone_hash |
| `backend/app/services/gmv.py` | 修改 | GMV 归因匹配改用 hash_phone() |
| `backend/app/api/v1/gmv.py` | 修改 | 响应中 phone_hash 脱敏处理 |
| `backend/alembic/versions/xxx_add_phone_encrypted.py` | 新建 | 数据库迁移 |
| `backend/tests/test_utils/test_crypto.py` | 新建 | 加密模块单元测试（9 个用例） |
| `backend/tests/test_services/test_member_crypto.py` | 新建 | 集成测试（4 个用例） |
| `backend/.env.example` | 新建/修改 | 提供加密密钥配置模板 |

## 10. 安全注意事项

1. **密钥绝不硬编码**：所有密钥从环境变量读取，开发环境提供 `.env.example` 模板
2. **IV 不可重用**：每次加密使用 CSPRNG 生成新的 12 字节 IV
3. **密文不可篡改**：GCM 模式提供认证，篡改密文会触发解密失败
4. **日志脱敏**：日志中不输出明文手机号或加密密钥
5. **测试隔离**：测试密钥与生产密钥严格分离
6. **pepper 不轮换**：HMAC pepper 轮换需重新计算所有 hash，设计上视为不可变
