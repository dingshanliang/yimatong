import os
import sys
from pathlib import Path

# 将 backend 目录加入 Python 路径
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

# 设置测试环境变量（在导入 app 之前）
os.environ.setdefault("database_url", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("redis_url", "redis://localhost:6379/0")
os.environ.setdefault("secret_key", "test-secret-key")
