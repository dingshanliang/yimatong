# 一码通后端

包装扫码增长 SaaS 平台后端服务。

## 技术栈

- Python 3.12+
- FastAPI
- SQLAlchemy 2.0 (async)
- PostgreSQL 16
- Redis 7

## 本地开发

### 前置条件

- Python 3.12+
- uv (Python 包管理器)
- Docker & Docker Compose (用于本地数据库服务)

### 安装依赖

```bash
cd backend
uv sync
```

### 启动开发服务

```bash
# 启动依赖服务
docker-compose -f ../docker-compose.dev.yml up -d postgres redis minio

# 启动后端
uv run uvicorn app.main:app --reload --port 8000
```

### 运行测试

```bash
uv run pytest
```

### 代码检查

```bash
uv run ruff check .
uv run ruff format --check .
```

## 项目结构

```
backend/
├── app/
│   ├── api/v1/          # API 路由
│   ├── core/            # 配置、数据库、依赖注入
│   ├── models/          # SQLAlchemy ORM 模型
│   ├── schemas/         # Pydantic 请求/响应模型
│   ├── services/        # 业务逻辑层
│   ├── tasks/           # 异步任务（arq）
│   ├── middleware/      # 中间件（tenant scope、限流等）
│   ├── utils/           # 工具函数
│   └── main.py          # FastAPI 入口
├── tests/               # 测试
├── alembic/             # 数据库迁移
└── pyproject.toml       # 项目配置
```
