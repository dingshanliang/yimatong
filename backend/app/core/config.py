from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://yimatong:yimatong@localhost:5432/yimatong_dev?ssl=disable"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-key-change-in-production"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # AES-256-GCM 加密密钥（hex 编码，按版本号）
    aes_master_key_v1: str = ""  # 阶段一主密钥
    aes_master_key_v2: str = ""  # 可选，密钥轮换时使用

    # HMAC-SHA256 pepper（hex 编码）
    hmac_pepper: str = ""

    # 后端对外地址（用于构建回调 URL 等）
    base_url: str = "http://localhost:8000"

    # CORS 配置（逗号分隔的前端域名）
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    # 平台管理员凭据
    platform_admin_email: str = "platform@yimatong.cn"
    platform_admin_password_hash: str = ""  # bcrypt hash

    # DeepSeek AI 配置
    deepseek_api_keys: str = ""  # 逗号分隔的多个 API key
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model_default: str = "deepseek-v4-flash"
    deepseek_model_advanced: str = "deepseek-v4-pro"
    ai_daily_limit_per_tenant: int = 100

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
