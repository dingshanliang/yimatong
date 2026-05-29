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

    # CORS 配置（逗号分隔的前端域名）
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
