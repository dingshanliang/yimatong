from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://yimatong:yimatong@localhost:5432/yimatong_dev?ssl=disable"
    migration_database_url: str | None = None
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

    # 接管域名真实核验：默认使用系统 DNS 和公网 443；本地/受控验收可指定独立解析器。
    takeover_dns_nameserver: str = ""
    takeover_dns_port: int = 53
    takeover_tls_port: int = 443
    takeover_tls_ca_file: str = ""

    # CORS 配置（逗号分隔的前端域名）
    cors_origins: str = (
        "http://localhost:3000,http://localhost:3001,http://localhost:3002,"
        "http://127.0.0.1:3000,http://127.0.0.1:3001,http://127.0.0.1:3002"
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

    @model_validator(mode="after")
    def _validate_auth_config(self) -> "Settings":
        if not self.secret_key:
            raise ValueError(
                "SECRET_KEY 环境变量未设置。生成方法: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        return self


settings = Settings()
