import ast
from pathlib import Path

from app.cli import baseline, seed
from scripts import seed_demo

_LIFECYCLE_FIELDS = {
    "revoked_at",
    "frozen_from_status",
    "frozen_at",
    "frozen_by",
    "freeze_reason",
    "freeze_provenance_version",
}


def _is_code_item_target(target: ast.expr) -> bool:
    if not isinstance(target, ast.Attribute):
        return False
    owner = target.value
    if isinstance(owner, ast.Name):
        return owner.id == "item"
    return isinstance(owner, ast.Subscript) and isinstance(owner.value, ast.Name) and owner.value.id == "items"


def _assigns_terminal_status(node: ast.Assign | ast.AnnAssign) -> bool:
    value = node.value
    return (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "CodeItemStatus"
        and value.attr in {"revoked", "frozen"}
    )


def test_official_cli_has_no_direct_code_item_lifecycle_writes() -> None:
    violations: list[str] = []
    for module in (seed, baseline, seed_demo):
        path = Path(module.__file__)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if _is_code_item_target(target) and (
                    target.attr in _LIFECYCLE_FIELDS or (target.attr == "status" and _assigns_terminal_status(node))
                ):
                    violations.append(f"{path.name}:{node.lineno}:{target.attr}")

    assert violations == []
