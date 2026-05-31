#!/usr/bin/env sh
set -eu

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  COMPOSE="docker compose"
fi

$COMPOSE -f docker-compose.dev.yml up -d --build --force-recreate \
  postgres \
  redis \
  minio \
  minio-init \
  migration \
  seed \
  backend \
  worker \
  mock-sms \
  mock-wechat \
  admin \
  h5

cat <<'EOF'

一码通 demo stack is starting.

Admin:   http://localhost:3000
H5:      http://localhost:3001
Backend: http://localhost:8000

Demo accounts:
  品牌管理员: admin@demo.com / Admin1234
  活动运营:   ops@demo.com / Ops123456
  代运营顾问: agency@demo.com / Agency1234
EOF
