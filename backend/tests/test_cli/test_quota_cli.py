from typer.testing import CliRunner

from app.cli.quota import app


def test_reconcile_requires_explicit_old_writer_drain_gate(monkeypatch):
    called = False

    async def must_not_run():
        nonlocal called
        called = True
        raise AssertionError("database reconciliation must not start")

    monkeypatch.setattr("app.cli.quota._reconcile_all", must_not_run)

    result = CliRunner().invoke(app, ["reconcile"])

    assert result.exit_code == 2
    assert "--old-writers-drained" in result.output
    assert called is False
