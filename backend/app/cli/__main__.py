"""一码通 CLI 入口。

聚合多个子命令组：
- `app.cli.seed`（演示数据）：`uv run python -m app.cli ...`
- `app.cli.baseline`（基准验收数据，yimatong-zgb1.1）：`uv run python -m app.cli baseline ...`

为保持向后兼容，根命令组直接暴露 seed 的子命令（`all` / `tenant` / `demo` 等），
并将 baseline 作为独立子命令组挂载。
"""

from app.cli import seed as seed_module

# 根命令组：直接复用 seed 的 Typer app，保持 `python -m app.cli all` 等旧用法不变。
app = seed_module.app

# 挂载基准数据子命令组：`python -m app.cli baseline build|verify`
from app.cli import baseline as baseline_module  # noqa: E402

app.add_typer(baseline_module.app, name="baseline", help="基准验收数据（yimatong-zgb1.1）")


if __name__ == "__main__":
    app()
