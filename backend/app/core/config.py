import math
from collections import Counter
from typing import Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

_KNOWN_SECRET_EXAMPLES = {
    "changeme",
    "change-me",
    "dev-secret-key-change-in-production",
    "replace-me",
    "secret",
    "test-secret-key",
    "your-secret-key",
    "yimatong-default-ip-hash-secret-change-in-production",
}
_KNOWN_SECRET_MARKERS = {
    "dev-secret-key-change-in-production",
    "test-secret-key",
    "yimatong-default-ip-hash-secret-change-in-production",
}


def _has_production_secret_strength(value: str) -> bool:
    """Reject short, sample, or clearly low-entropy application secrets."""

    candidate = value.strip()
    lowered = candidate.lower()
    if (
        len(candidate.encode("utf-8")) < 32
        or lowered in _KNOWN_SECRET_EXAMPLES
        or any(marker in lowered for marker in _KNOWN_SECRET_MARKERS)
    ):
        return False
    counts = Counter(candidate)
    if len(counts) < 12:
        return False
    estimated_bits = -sum(count * math.log2(count / len(candidate)) for count in counts.values())
    return estimated_bits >= 192


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+asyncpg://yimatong:yimatong@localhost:5432/yimatong_dev?ssl=disable"
    migration_database_url: str | None = None
    control_database_url: str | None = None
    callback_database_url: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = ""  # 必须通过环境变量 SECRET_KEY 设置
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    # AES-256-GCM 加密密钥（hex 编码，按版本号）
    aes_master_key_v1: str = ""  # 阶段一主密钥
    aes_master_key_v2: str = ""  # 可选，密钥轮换时使用

    # HMAC-SHA256 pepper（hex 编码）
    hmac_pepper: str = ""

    # IP 哈希加盐密钥（防止彩虹表攻击，生产环境必须修改）
    ip_hash_secret: str = Field(default="yimatong-default-ip-hash-secret-change-in-production")

    # 后端对外地址（用于构建回调 URL 等）
    base_url: str = "http://localhost:8000"

    # 品牌方 Admin 的唯一公网基址（注册链接、激活链接、密码重置链接）
    admin_public_url: str = "http://localhost:3000"
    h5_public_url: str = "http://localhost:3001"
    platform_public_url: str = "http://localhost:3002"

    # 一码通共享会员小程序。真实 AppID/Secret 在外部交付 smoke 前保持为空；
    # H5 降级路径不依赖这两个配置。
    shared_wechat_miniprogram_appid: str = ""
    shared_wechat_miniprogram_secret: str = ""
    wechat_subscription_template_ids: dict[str, str] = Field(default_factory=dict)

    # 接管域名真实核验：默认使用系统 DNS 和公网 443；本地/受控验收可指定独立解析器。
    takeover_dns_nameserver: str = ""
    takeover_dns_port: int = 53
    takeover_tls_port: int = 443
    takeover_tls_ca_file: str = ""

    # Only direct peers in these networks may supply X-Forwarded-For.
    trusted_proxy_cidrs: str = "127.0.0.1/32,::1/128"

    # CORS 配置（逗号分隔的前端域名）
    cors_origins: str = (
        "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003,"
        "http://127.0.0.1:3000,http://127.0.0.1:3001,http://127.0.0.1:3002,http://127.0.0.1:3003"
    )

    # 平台管理员凭据
    platform_admin_email: str = "platform@yimatong.cn"
    platform_admin_password_hash: str = ""  # bcrypt hash

    # DeepSeek AI 配置
    deepseek_api_keys: str = ""  # 逗号分隔的多个 API key
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model_default: str = "deepseek-v4-flash"
    deepseek_model_advanced: str = "deepseek-v4-pro"
    ai_daily_limit_per_tenant: int = 100

    # Logging
    log_level: str = "INFO"
    log_format: str = "human"  # "human" | "json"

    # MinIO / S3
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "yimatong"

    # Cookie 安全配置
    cookie_domain: str = ""
    cookie_secure: bool = False  # 生产环境必须设为 True
    cookie_samesite: str = "Lax"  # 或 "Strict"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @field_validator("admin_public_url")
    @classmethod
    def _validate_admin_public_url(cls, value: str) -> str:
        candidate = value.strip()
        parsed = urlsplit(candidate)
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("ADMIN_PUBLIC_URL 端口格式无效") from exc
        if (
            any(character.isspace() for character in candidate)
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("ADMIN_PUBLIC_URL 必须是无路径、查询参数或凭据的完整 http(s) 基址")
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    @field_validator("platform_public_url")
    @classmethod
    def _validate_platform_public_url(cls, value: str) -> str:
        candidate = value.strip()
        parsed = urlsplit(candidate)
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("PLATFORM_PUBLIC_URL 端口格式无效") from exc
        if (
            any(character.isspace() for character in candidate)
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("PLATFORM_PUBLIC_URL 必须是无路径、查询参数或凭据的完整 http(s) 基址")
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    @field_validator("h5_public_url")
    @classmethod
    def _validate_h5_public_url(cls, value: str) -> str:
        candidate = value.strip()
        parsed = urlsplit(candidate)
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("H5_PUBLIC_URL 端口格式无效") from exc
        if (
            any(character.isspace() for character in candidate)
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("H5_PUBLIC_URL 必须是无路径、查询参数或凭据的完整 http(s) 基址")
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    @model_validator(mode="after")
    def _validate_auth_config(self) -> "Settings":
        if not self.secret_key:
            raise ValueError(
                "SECRET_KEY 环境变量未设置。生成方法: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        if self.environment == "production":
            if not self.control_database_url:
                raise ValueError("生产环境必须显式配置独立的 CONTROL_DATABASE_URL")
            if self.control_database_url == self.database_url:
                raise ValueError("生产环境 CONTROL_DATABASE_URL 必须与普通 DATABASE_URL 使用不同凭据")
            if not self.callback_database_url:
                raise ValueError("生产环境必须显式配置独立的 CALLBACK_DATABASE_URL")
            if self.callback_database_url in {
                self.database_url,
                self.control_database_url,
                self.migration_database_url,
            }:
                raise ValueError("生产环境 CALLBACK_DATABASE_URL 必须使用独立的最小权限凭据")
            if not _has_production_secret_strength(self.secret_key):
                raise ValueError("生产环境 SECRET_KEY 必须使用至少 32 字节的高熵随机值，且不能使用示例或默认值")
            if not _has_production_secret_strength(self.hmac_pepper):
                raise ValueError("生产环境 HMAC_PEPPER 必须使用独立的高熵随机值，且不能使用示例或默认值")
            if self.hmac_pepper.strip() == self.secret_key.strip():
                raise ValueError("生产环境 HMAC_PEPPER 必须与其他应用密钥相互独立")
            if not _has_production_secret_strength(self.ip_hash_secret):
                raise ValueError("生产环境 IP_HASH_SECRET 必须使用独立的高熵随机值，且不能使用示例或默认值")
            if self.ip_hash_secret.strip() in {self.secret_key.strip(), self.hmac_pepper.strip()}:
                raise ValueError("生产环境 IP_HASH_SECRET 必须与其他应用密钥相互独立")
            parsed = urlsplit(self.admin_public_url)
            if parsed.scheme != "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("生产环境必须显式配置公网 HTTPS ADMIN_PUBLIC_URL")
            platform = urlsplit(self.platform_public_url)
            if platform.scheme != "https" or platform.hostname in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("生产环境必须显式配置公网 HTTPS PLATFORM_PUBLIC_URL")
            h5 = urlsplit(self.h5_public_url)
            if h5.scheme != "https" or h5.hostname in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("生产环境必须显式配置公网 HTTPS H5_PUBLIC_URL")
            if not self.cookie_secure:
                raise ValueError("生产环境平台认证 Cookie 必须启用 COOKIE_SECURE")
            if self.cookie_samesite.lower() == "none":
                raise ValueError("生产环境平台认证 Cookie 禁止 COOKIE_SAMESITE=None")
        return self

    def build_admin_url(self, path: str, query: dict[str, str] | None = None) -> str:
        """基于 canonical Admin 公网基址生成面向用户的完整链接。"""
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Admin URL path 必须是以单个 / 开头的站内路径")
        query_string = urlencode(query or {})
        return f"{self.admin_public_url}{path}{f'?{query_string}' if query_string else ''}"


settings = Settings()
