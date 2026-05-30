# 一码通前端 Monorepo

pnpm workspace monorepo，包含管理后台和消费者扫码页。

## 结构

```
apps/admin/     管理后台 — Next.js + Ant Design（端口 3000）
apps/h5/        消费者扫码页 — Next.js + Tailwind + Headless UI（端口 3001）
packages/shared/ 共享 TypeScript 类型定义
```

## 开发命令

```bash
pnpm dev:admin      # 启动 Admin 开发服务器
pnpm dev:h5         # 启动 H5 开发服务器
pnpm build          # 构建全部
pnpm build:admin    # 只构建 Admin
pnpm build:h5       # 只构建 H5
pnpm build:shared   # 只构建共享包（admin/h5 依赖此包）
pnpm lint:admin     # Lint Admin
pnpm lint:h5        # Lint H5
pnpm test:e2e       # Playwright E2E 测试
pnpm test:e2e:ui    # E2E 测试（带 UI）
```

## 技术约束

- Next.js 16（App Router），React 19
- Admin: Ant Design 6 + zustand + axios
- H5: Tailwind CSS 4 + Headless UI + zustand + axios
- 共享包 `@yimatong/shared` 使用 `workspace:*` 协议
- 后端 API 地址：`NEXT_PUBLIC_API_URL` 环境变量（默认 http://localhost:8000）

## AGENTS.md

@AGENTS.md
