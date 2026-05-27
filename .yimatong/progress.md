# 一码通开发经验积累

> 此文件由自主开发循环自动追加。每次迭代发现新模式、踩坑、约定都记录于此。

## 格式

每个 Wave 完成后追加一个区块：

```
## Wave {id}
### 发现的模式
- ...

### 踩过的坑
- ...

### 代码约定
- ...
```

## Wave A1
### 发现的模式
- hatchling 需要显式配置 `[tool.hatch.build.targets.wheel] packages = ["app"]`
- pytest 工作目录必须 cd 到 backend/ 下才能正确发现测试
- passlib + bcrypt 有版本兼容问题，直接用 bcrypt 库替代
- ruff 的 N818 规则要求异常类以 Error 后缀命名

### 踩过的坑
- uv sync 在项目根目录找不到 pyproject.toml，必须 cd backend/
- passlib 和 bcrypt 新版不兼容：ValueError: password cannot be longer than 72 bytes
- Write 工具创建新文件有时会报 "File has not been read yet" 错误（不一致）

### 代码约定
- 使用 uuid6 的 uuid7 作为主键（时间排序 UUID）
- 异常类命名：`SomethingError`（遵循 ruff N818）
- 测试文件路径：`tests/test_{module}/test_{feature}.py`
- 导入排序：stdlib → third-party → local (ruff isort 自动处理)
