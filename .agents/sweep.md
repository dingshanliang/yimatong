# Sweep 扫描基线 — 2026-05-28

## 项目阶段
Phase 1 Alpha/Beta 开发中，大量 Phase 2/3 基础设施已预写但尚未接入。

## 扫描结果摘要
- Python (vulture + deadcode): ~150 候选，9 项真正的"未被导入"（均为预留基础设施，非死代码）
- TS Admin (knip): 5 项（hooks.ts 文件 + 2 导出 + 2 类型 + 1 依赖）
- TS H5 (knip): 6 项（4 未使用依赖 + 1 未使用导出 + 1 未使用 devDep）

## 排除规则（此项目特有）
- FastAPI `@router.*` 装饰器注册的端点函数：deadcode 无法识别，全部为 false positive
- SQLAlchemy `Mapped[]` 模型字段：映射 DB 列，无 Python 引用不代表死代码
- Pydantic `model_config` / schema 字段：用于序列化验证
- 以下模块为 Phase 2/3 预留，不算死代码：
  - `app/middleware/rate_limit.py` → Phase 1 码解析限流
  - `app/services/circuit_breaker.py` → Phase 2 外部连接器
  - `app/utils/rls.py` → RLS 事务级隔离
  - `app/services/quota.py` → 配额检查
  - `app/services/public_id.py:validate_public_id` → 码验证
  - `app/services/page_render.py:invalidate_cache` → 缓存失效
  - `app/services/resolve_cache.py` → 解析缓存
  - `app/schemas/product.py:ProductionBatchUpdate` → 生产批次编辑

## 建议下次扫描时机
Phase 2 开发完成后（接入限流、连接器、风控看板等），重新跑 sweep 做增量对比。
