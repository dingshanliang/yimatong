#!/usr/bin/env bash
# 一码通 demo 环境部署脚本（目标：szxc-02 共享主机）。
#
# 用法（仓库根目录执行）：
#   scripts/deploy-demo.sh              # 首次部署或常规更新（rsync 代码 + 迁移 + 重启，保留数据）
#   scripts/deploy-demo.sh --reseed     # 额外重跑 `app.cli all` 完整 seed（覆盖演示数据）
#   scripts/deploy-demo.sh --no-build   # 跳过后端镜像构建（依赖未变时）
#
# 布局与边界见 docker-compose.demo.yml 头部注释。
# 密钥只在服务器部署目录生成（.env / init_runtime_role.sql / CREDENTIALS.txt，均 600），
# 本脚本与仓库不落任何密码。
set -euo pipefail

HOST="${DEMO_HOST:-szxc-02}"
DEPLOY_DIR="${DEMO_DIR:-/home/eric/deployments/yimatong-demo}"
PUBLIC_IP="${DEMO_PUBLIC_IP:-106.227.95.149}"
ADMIN_PORT="${DEMO_ADMIN_PORT:-9120}"
H5_PORT="${DEMO_H5_PORT:-9121}"
PLATFORM_PORT="${DEMO_PLATFORM_PORT:-9122}"
API_PORT="${DEMO_API_PORT:-9123}"
UV_INDEX_URL="${UV_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"

RESEED=0
BUILD=1
for arg in "$@"; do
  case "$arg" in
    --reseed) RESEED=1 ;;
    --no-build) BUILD=0 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

PUBLIC_API_URL="http://${PUBLIC_IP}:${API_PORT}"

log() { printf '\033[1;32m[deploy]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[deploy][FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

ssh_run() { ssh -o ConnectTimeout=20 "$HOST" "$@"; }

# ---------- 1. 预检 ----------
[ -f docker-compose.demo.yml ] || die "缺少 docker-compose.demo.yml（请在仓库根目录执行）"

log "预检 $HOST ..."
ssh_run "true" || die "SSH 不可达"
if ssh_run "ss -tln | grep -E ':(${ADMIN_PORT}|${H5_PORT}|${PLATFORM_PORT}|${API_PORT}) '" >/dev/null 2>&1; then
  die "目标端口 ${ADMIN_PORT}-${API_PORT} 中有已被占用者，先确认归属再重试"
fi

# 记录共享主机上既有业务容器的基线，部署后必须原样。
ssh_run "mkdir -p '$DEPLOY_DIR'"
ssh_run "for c in gts-frontend gts-backend fsc-cfm-frontend fsc-cfm-backend; do
    printf '%s %s\n' \"\$c\" \"\$(docker inspect -f '{{.State.Status}}/{{.State.StartedAt}}' \$c 2>/dev/null || echo missing)\";
  done > '$DEPLOY_DIR/.baseline-containers'"
log "已记录 gts-* / fsc-cfm-* 容器基线"

# ---------- 2. 同步代码与 compose ----------
log "rsync 源码到 $HOST:$DEPLOY_DIR/current ..."
rsync -az --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude '.pnpm-store' \
  --exclude '.venv' \
  --exclude '.next' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache' \
  --exclude '.ruff_cache' \
  --exclude '.DS_Store' \
  --exclude '.env' \
  --exclude 'backend/.env' \
  --exclude 'e2e/test-results' \
  --exclude 'e2e/playwright-report' \
  ./ "$HOST:$DEPLOY_DIR/current/"
scp -q docker-compose.demo.yml "$HOST:$DEPLOY_DIR/docker-compose.yml"

# ---------- 3. 首次生成 .env（远端，幂等：存在即跳过） ----------
log "准备服务器端 .env ..."
ssh_run "bash -s" -- "$DEPLOY_DIR" "$ADMIN_PORT" "$H5_PORT" "$PLATFORM_PORT" "$API_PORT" \
  "$PUBLIC_IP" "$UV_INDEX_URL" <<'REMOTE_ENV'
set -euo pipefail
cd "$1"; admin_port=$2; h5_port=$3; platform_port=$4; api_port=$5; public_ip=$6; uv_index=$7
if [ -f .env ]; then
  echo "ENV_GENERATED=0"
  exit 0
fi
gen_hex() { openssl rand -hex "$1"; }
admin_url="http://${public_ip}:${admin_port}"
h5_url="http://${public_ip}:${h5_port}"
platform_url="http://${public_ip}:${platform_port}"
api_url="http://${public_ip}:${api_port}"
cors="${admin_url},${h5_url},${platform_url}"
umask 077
cat > .env <<ENV
# yimatong demo（szxc-02）— 服务器受控文件，勿复制出本机
ENVIRONMENT=development
UV_INDEX_URL=${uv_index}

POSTGRES_PASSWORD=$(gen_hex 24)
APP_DB_PASSWORD=$(gen_hex 24)
CALLBACK_DB_PASSWORD=$(gen_hex 24)

SECRET_KEY=$(gen_hex 32)
AES_MASTER_KEY_V1=$(gen_hex 32)
HMAC_PEPPER=$(gen_hex 32)
IP_HASH_SECRET=$(gen_hex 32)

MINIO_ROOT_USER=yimatong-demo
MINIO_ROOT_PASSWORD=$(gen_hex 24)

PLATFORM_ADMIN_EMAIL=platform@yimatong.cn
PLATFORM_ADMIN_PASSWORD_HASH=__PENDING_BCRYPT__

ADMIN_PUBLIC_URL=${admin_url}
H5_PUBLIC_URL=${h5_url}
PLATFORM_PUBLIC_URL=${platform_url}
PUBLIC_API_URL=${api_url}
CORS_ORIGINS=${cors}

DEMO_ADMIN_PORT=${admin_port}
DEMO_H5_PORT=${h5_port}
DEMO_PLATFORM_PORT=${platform_port}
DEMO_API_PORT=${api_port}
ENV
chmod 600 .env
echo "ENV_GENERATED=1"
REMOTE_ENV

# ---------- 4. 生成受限角色 SQL（仓库审计版 + 随机口令替换，每次部署重放） ----------
# init_runtime_role.sql 是封闭权限集 + RLS 白名单授权脚本，只能在迁移后重放；
# 这里只替换其中的开发口令字面量，授权逻辑原样使用仓库审计版本。
# init_role_bootstrap.sql 是迁移前的最小建角色脚本（迁移中的 REVOKE/GRANT 直接引用角色）。
ssh_run "bash -s" -- "$DEPLOY_DIR" <<'REMOTE_SQL'
set -euo pipefail
cd "$1"
app_pw=$(grep -E '^APP_DB_PASSWORD=' .env | cut -d= -f2-)
cb_pw=$(grep -E '^CALLBACK_DB_PASSWORD=' .env | cut -d= -f2-)
umask 077
sed -e "s|PASSWORD 'yimatong_app'|PASSWORD '${app_pw}'|g" \
    -e "s|PASSWORD 'yimatong_callback'|PASSWORD '${cb_pw}'|g" \
    current/backend/scripts/init_runtime_role.sql > init_runtime_role.sql
cat > init_role_bootstrap.sql <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'yimatong_app') THEN
        CREATE ROLE yimatong_app LOGIN PASSWORD '${app_pw}'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'yimatong_callback') THEN
        CREATE ROLE yimatong_callback LOGIN PASSWORD '${cb_pw}'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
END
\$\$;
SQL
chmod 600 init_runtime_role.sql init_role_bootstrap.sql
echo "SQL_GENERATED=1"
REMOTE_SQL

# ---------- 5. 构建后端镜像 ----------
if [ "$BUILD" -eq 1 ]; then
  log "构建后端镜像（PyPI: ${UV_INDEX_URL}）..."
  ssh_run "cd '$DEPLOY_DIR' && docker compose build backend" || die "后端镜像构建失败"
else
  log "跳过镜像构建（--no-build）"
fi

# ---------- 6. 平台管理员口令（首次） ----------
if ssh_run "cd '$DEPLOY_DIR' && grep -q '__PENDING_BCRYPT__' .env"; then
  log "生成平台管理员口令与 bcrypt hash ..."
  ssh_run "bash -s" -- "$DEPLOY_DIR" <<'REMOTE_PW' || die "平台管理员口令生成失败"
set -euo pipefail
cd "$1"
platform_pw="Demo-$(openssl rand -hex 4)"
hash=$(docker compose run --rm -T --no-deps backend uv run python -c \
  "import bcrypt,sys; print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())" "$platform_pw")
# compose 会对 .env 值里的 $ 做插值，bcrypt hash 必须以 $$ 转义写入
esc=${hash//\$/\$\$}
sed -i.bak "s|^PLATFORM_ADMIN_PASSWORD_HASH=.*|PLATFORM_ADMIN_PASSWORD_HASH=${esc}|" .env && rm -f .env.bak
platform_email=$(grep -E '^PLATFORM_ADMIN_EMAIL=' .env | cut -d= -f2-)
umask 077
cat > CREDENTIALS.txt <<CRED
# yimatong demo（szxc-02）— 服务器受控文件，勿复制出本机
PLATFORM_ADMIN_EMAIL=${platform_email}
PLATFORM_ADMIN_PASSWORD=${platform_pw}
# 品牌方 demo 租户账号见 current/backend/scripts/seed_demo.py 的 DEMO_ACCOUNTS
#（admin@demo.com / Admin1234 等，为演示专用口令，非生产凭证）
CRED
chmod 600 CREDENTIALS.txt
grep -q '^PLATFORM_ADMIN_PASSWORD_HASH=\$\$2' .env || { echo 'hash 写入异常'; exit 1; }
echo "PLATFORM_PW_READY"
REMOTE_PW
fi

# ---------- 7. 基础设施 + 一次性初始化 ----------
log "启动 PostgreSQL / Redis / MinIO ..."
ssh_run "cd '$DEPLOY_DIR' && docker compose up -d --wait postgres redis minio" || die "基础设施启动失败"

log "初始化 MinIO 桶 ..."
ssh_run "cd '$DEPLOY_DIR' && docker compose --profile tools run --rm minio-init" || die "MinIO 初始化失败"

# 引导顺序（实证）：迁移中的 REVOKE/GRANT 直接引用 yimatong_app，角色必须先存在；
# 全量授权脚本的断言要求迁移后的库状态，只能在迁移后重放。
log "创建受限运行角色（最小引导）..."
ssh_run "cd '$DEPLOY_DIR' && docker compose --profile tools run --rm db-roles-bootstrap" || die "数据库角色引导失败"

log "执行 Alembic 迁移 ..."
ssh_run "bash -s" -- "$DEPLOY_DIR" <<'REMOTE_MIGRATE' || die "数据库迁移失败"
set -euo pipefail
cd "$1"
pg_pw=$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)
docker compose run --rm -T \
  -e "database_url=postgresql+asyncpg://yimatong:${pg_pw}@postgres:5432/yimatong" \
  backend uv run alembic upgrade head
REMOTE_MIGRATE

log "重放运行角色授权（迁移后补齐白名单 DML）..."
ssh_run "cd '$DEPLOY_DIR' && docker compose --profile tools run --rm db-roles" || die "数据库角色授权重放失败"

# 首次部署（无租户数据）或显式 --reseed 时执行完整 seed
NEED_SEED=$RESEED
TENANT_ROWS=$(ssh_run "cd '$DEPLOY_DIR' && docker compose exec -T postgres psql -U yimatong -d yimatong -tAc \"SELECT 1 FROM tenants LIMIT 1\" 2>/dev/null | tr -d '[:space:]'")
if [ -z "$TENANT_ROWS" ]; then
  NEED_SEED=1
fi
if [ "$NEED_SEED" -eq 1 ]; then
  log "执行完整 seed（app.cli all）..."
  ssh_run "bash -s" -- "$DEPLOY_DIR" <<'REMOTE_SEED' || die "seed 失败"
set -euo pipefail
cd "$1"
pg_pw=$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)
docker compose run --rm -T \
  -e "migration_database_url=postgresql+asyncpg://yimatong:${pg_pw}@postgres:5432/yimatong" \
  backend uv run python -m app.cli all
REMOTE_SEED
else
  log "跳过 seed（数据库已有租户数据；需要重置演示数据用 --reseed）"
fi

# ---------- 8. 启动全栈 ----------
log "启动全部服务 ..."
ssh_run "cd '$DEPLOY_DIR' && docker compose up -d" || die "服务启动失败"

# ---------- 9. 冒烟 ----------
log "等待后端 /health ..."
ssh_run "cd '$DEPLOY_DIR' && timeout 240 bash -c 'until curl -sf http://localhost:${API_PORT}/health >/dev/null 2>&1; do sleep 5; done'" \
  || die "后端健康检查超时（240s）"
log "后端 /health OK"

log "验证品牌 Admin 登录（demo 租户）..."
LOGIN_BODY='{"email":"admin@demo.com","password":"Admin1234","tenant_slug":"demo"}'
LOGIN_CODE=$(ssh_run "curl -s -o /tmp/yimatong-login.json -w '%{http_code}' -X POST http://localhost:${API_PORT}/api/v1/auth/login -H 'Content-Type: application/json' -d '${LOGIN_BODY}'")
if [ "$LOGIN_CODE" = "200" ] && ssh_run "grep -q access_token /tmp/yimatong-login.json"; then
  log "Admin 登录 OK"
else
  ssh_run "head -c 300 /tmp/yimatong-login.json" || true
  die "Admin 登录冒烟失败（HTTP $LOGIN_CODE）"
fi

log "等待前端入口就绪（dev server 首次安装+编译较慢）..."
for entry in "Admin:${ADMIN_PORT}/login" "H5:${H5_PORT}/" "Platform:${PLATFORM_PORT}/login"; do
  name="${entry%%:*}"; path_port="${entry#*:}"; port="${path_port%%/*}"; path="${path_port#*/}"
  [ -n "$path" ] || path="/"
  code=$(ssh_run "timeout 900 bash -c '
    while :; do
      c=\$(curl -s -o /dev/null -w \"%{http_code}\" --max-time 30 http://localhost:${port}${path} 2>/dev/null) || c=000
      case \$c in 200|301|302|307|308) echo \$c; exit 0;; esac
      sleep 5
    done
    echo TIMEOUT
  '")
  [ "$code" != "TIMEOUT" ] || die "$name 前端 ${path} 等待超时（900s）"
  log "  $name http://${PUBLIC_IP}:${port}${path} -> $code"
done

# ---------- 10. 共享主机隔离性检查 ----------
log "隔离性检查（9000/9002 归属与既有容器状态）..."
ssh_run "bash -s" -- "$DEPLOY_DIR" <<'REMOTE_CHECK'
set -euo pipefail
cd "$1"
owner_9000=$(docker ps --filter publish=9000 --format '{{.Names}}')
owner_9002=$(docker ps --filter publish=9002 --format '{{.Names}}')
[ "$owner_9000" = "gts-frontend" ] || { echo "9000 归属异常: $owner_9000"; exit 1; }
[ "$owner_9002" = "fsc-cfm-frontend" ] || { echo "9002 归属异常: $owner_9002"; exit 1; }
while read -r name state; do
  now=$(docker inspect -f '{{.State.Status}}/{{.State.StartedAt}}' "$name" 2>/dev/null || echo missing)
  [ "$now" = "$state" ] || { echo "$name 状态变化: $state -> $now"; exit 1; }
done < .baseline-containers
echo "ISOLATION_OK"
REMOTE_CHECK

log "部署完成。"
log "  Admin    : http://${PUBLIC_IP}:${ADMIN_PORT}"
log "  H5       : http://${PUBLIC_IP}:${H5_PORT}"
log "  Platform : http://${PUBLIC_IP}:${PLATFORM_PORT}"
log "  API      : ${PUBLIC_API_URL}"
log "平台管理员口令在 $HOST:$DEPLOY_DIR/CREDENTIALS.txt；demo 租户账号见 seed_demo.py DEMO_ACCOUNTS。"
