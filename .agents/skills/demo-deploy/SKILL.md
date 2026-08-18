---
name: demo-deploy
description: Deploy or update the Yimatong demo environment on server szxc-02. Use whenever the user asks to 部署/发布/更新 demo 环境、上服务器、更新 szxc-02、重置演示数据（--reseed）、查看 demo 环境日志/状态/重启， or reports the demo being broken (login failures, stale code, container issues). Covers local image build-and-ship, scripts/deploy-demo.sh, shared-host isolation rules, and post-deploy smoke.
---

# Yimatong Demo Deploy (szxc-02)

## Fixed facts

| Item                         | Value                                                                                                                                                          |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Host                         | `szxc-02` (106.227.95.149, shared host — see red lines below)                                                                                                  |
| Remote dir                   | `/home/eric/deployments/yimatong-demo` (`current/` = rsynced source; secrets live only here, chmod 600)                                                        |
| URLs                         | Admin `:9120` · H5 `:9121` · Platform `:9122` · API `:9123`                                                                                                    |
| Compose project / containers | `yimatong-demo` / prefix `yimatong-`                                                                                                                           |
| Demo accounts                | brand tenant accounts in `backend/scripts/seed_demo.py` DEMO_ACCOUNTS (`admin@demo.com` / `Admin1234`); platform admin password on server in `CREDENTIALS.txt` |
| Deploy script                | `scripts/deploy-demo.sh` (run from repo root)                                                                                                                  |

Two intentional design choices — do not "fix" them without HTTPS:

- Frontends run as Next **dev servers** (same shape as `docker-compose.dev.yml`). A production build hard-requires HTTPS (`H5 CSP build throws on http:// API`; backend `ENVIRONMENT=production` enforces HTTPS + `COOKIE_SECURE`). Demo is plain HTTP by port policy.
- `ENVIRONMENT=development` in the server `.env` is deliberate for the same reason.

## Choose the update path

Ask: what changed since the last deploy?

1. **Backend dependencies, `backend/Dockerfile`, or `backend/.dockerignore` changed** → rebuild the image LOCALLY and ship it (never `docker compose build` on the server — ghcr and big PyPI wheels crawl at ~20KB/s there):

   ```bash
   docker buildx build --platform linux/amd64 -t yimatong/backend:demo -f backend/Dockerfile backend/
   docker save yimatong/backend:demo | gzip | ssh szxc-02 'gunzip | docker load'
   ssh szxc-02 'cd /home/eric/deployments/yimatong-demo && docker volume rm yimatong-demo_backend_venv' # re-init venv from new image
   scripts/deploy-demo.sh --no-build
   ```

   Reset the `backend_venv` volume whenever the image changed, or `uv run` may reconcile against a stale venv.

2. **Backend/ frontend source only** → the script rsyncs code and restarts backend/worker itself:

   ```bash
   scripts/deploy-demo.sh --no-build
   ```

   Frontend dev servers hot-reload from the bind mount. Exception: if `frontend/pnpm-lock.yaml` changed, also `ssh szxc-02 'cd /home/eric/deployments/yimatong-demo && docker compose restart admin h5 platform'` so `pnpm install` re-runs (~1–3 min, warm volumes).

3. **Demo data stale / user wants a clean demo** → add `--reseed` (re-runs `app.cli all`; not needed otherwise — the script seeds only when the DB has no tenants).

4. **First deploy on a fresh volume** → plain `scripts/deploy-demo.sh` (script generates server-side `.env`/role SQL/CREDENTIALS only if missing; it never rotates existing secrets).

Always run from the repo root; the script is idempotent and safe to re-run after a failure — it prints `[deploy][FAIL]` with the failing stage. Long runs (frontend first install) can exceed a single Bash timeout: run it in the background and poll the log.

## What the script does (in order)

Preflight (ports 9120–9123 free, records gts-_/fsc-cfm-_ baseline) → rsync source (excludes `.git`/`node_modules`/`.pnpm-store`/`.venv`/`.next`) → generate server-only secrets if absent → build backend image unless `--no-build` → platform-admin bcrypt (writes `$$`-escaped hash into `.env`) → infra up → MinIO buckets → **minimal role bootstrap → Alembic migrate → audited role grant replay** (this order is load-bearing: migrations REVOKE/GRANT against `yimatong_app`, but the grant script's registry assertion requires a post-migration DB) → seed if needed → `up -d` + restart backend/worker → smoke (health, demo login, frontend entries) → isolation re-check.

Read-only ops against the stack:

```bash
ssh szxc-02 'cd /home/eric/deployments/yimatong-demo && docker compose ps'
ssh szxc-02 'docker logs yimatong-backend --tail 50'        # same for worker/admin/h5/platform
ssh szxc-02 'cd /home/eric/deployments/yimatong-demo && docker compose restart backend worker'
```

## Shared-host red lines (szxc-02 also runs GTS demo and FSC-CFM production)

- Never touch `gts-*` / `fsc-cfm-*` containers or their data; no `docker system prune`, no `down -v` beyond the yimatong project.
- Ports 9000/9002 (plus 9109/9110/9173) belong to other stacks; yimatong owns only 9120–9123 (all app ports must stay within 9000–10000).
- After any deploy, confirm `9000=gts-frontend`, `9002=fsc-cfm-frontend`, and that the four baseline containers' `StartedAt` are unchanged (the script's final step does this; on failure it prints the offender).
- Server inventory and port rules live in GBrain `notes/personal-cloud-server-inventory`; CN-cloud deployment pitfalls (including this stack's) live in `notes/deploy-postgres-from-sqlite-tests-cn-cloud`.

## Failure quick reference

| Symptom                                              | Cause / fix                                                                                                                                       |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Platform login `Invalid salt`                        | `.env` hash lost `$` to compose interpolation — write `$$`-escaped (script does this; verify `grep PLATFORM_ADMIN_PASSWORD_HASH` shows `$$2b$$…`) |
| Server build impossibly slow / UV timeouts           | Expected; use the local buildx + `docker save/load` path above                                                                                    |
| Migration fails `role "yimatong_app" does not exist` | Role bootstrap was skipped or DB volume was reset — rerun the script (bootstrap runs before migrate)                                              |
| Role script `registry count is inconsistent`         | Audited grant script ran against a pre-migration DB — keep the script's ordering; don't run `db-roles` on an empty database by hand               |
| Seed `permission denied for table …`                 | Grants missing — rerun the post-migration `db-roles` replay step                                                                                  |
| Backend serving stale code after deploy              | `restart backend worker` (bind-mounted code + no `--reload`); the script does this since 2026-08-18                                               |
| Frontend 000 / not listening                         | Dev server still installing deps (first boot ~5–10 min); check `docker logs yimatong-admin`, don't restart mid-install                            |

## Report back

After a deploy or update, report: entry URLs, what changed, smoke results (login OK, frontends 200, isolation OK), and where any failure happened. Never paste server secrets, hashes, or the platform admin password into the chat or the repo.
