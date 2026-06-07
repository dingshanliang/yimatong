#!/usr/bin/env sh
set -eu

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  COMPOSE="docker compose"
fi

echo "Starting infrastructure services (Postgres, Redis, MinIO, Mock)..."
$COMPOSE -f docker-compose.infra.yml up -d --build --force-recreate \
  postgres \
  redis \
  minio \
  minio-init \
  mock-sms \
  mock-wechat

cat <<'EOF'

一码通基础设施已启动。

Services:
  Postgres:  localhost:5433  (Docker)  或 localhost:5432 (本地 PG)
  Redis:     localhost:6380
  MinIO:     localhost:9000  /  localhost:9001
  Mock SMS:  localhost:3099
  Mock WX:   localhost:3098

本地启动后端:
  cd backend && source .venv/bin/activate && uv run uvicorn app.main:app --reload

本地启动前端:
  cd frontend && pnpm dev:admin    # http://localhost:3000
  cd frontend && pnpm dev:h5       # http://localhost:3001

数据库迁移（如需）:
  cd backend && alembic upgrade head

Seed 数据（如需）:
  cd backend && uv run python -m app.cli all
EOF
