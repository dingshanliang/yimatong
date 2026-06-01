---
name: wave-verify
description: Verify wave completion against strategy document acceptance criteria
disable-model-invocation: true
---

# Wave 验收工作流

## 1. 读取状态
- 读取 `.yimatong/project-state.json` 找到最近完成的 wave
- 读取对应 `.yimatong/wave-specs/{wave}.json`

## 2. 逐条验证
对每个 story：
- 检查 acceptance_criteria 中的每一条
- 运行关联的测试文件
- 记录通过/失败状态

## 3. 黄金链路测试
运行 5 条黄金链路 E2E（已实现的那些）：
1. 租户隔离
2. 产品到扫码页
3. 首扫/重扫
4. 权益领取幂等（Beta 才有）
5. 导出与审计（Beta 才有）

## 4. 代码质量检查
- 运行 ruff check
- 检查测试覆盖率
- 确认所有 migration 有 downgrade

## 5. 生成验收报告
输出到 `.yimatong/verification/{wave}-report.md`：
- Stories: X/Y passed
- Golden paths: 状态
- Coverage: 百分比
- Issues: 未通过项清单
