#!/usr/bin/env python3
"""Run the versioned four-layer member-repurchase evidence pack."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "docs/03_delivery/member-repurchase-loop/evidence-manifest.v1.json"

COMMANDS = (
    (
        "backend-static-and-contract",
        REPO_ROOT / "backend",
        [
            "uv",
            "run",
            "ruff",
            "check",
            "app/cli/baseline.py",
            "app/services/campaign_authority.py",
            "tests/test_acceptance/verifier.py",
            "tests/test_acceptance/test_baseline_rebuild.py",
        ],
    ),
    (
        "backend-member-repurchase-unit",
        REPO_ROOT / "backend",
        [
            "uv",
            "run",
            "pytest",
            "tests/test_services/test_brand_membership.py",
            "tests/test_services/test_repurchase_coupon.py",
            "tests/test_services/test_commerce_integration.py",
            "tests/test_services/test_repurchase_workbench.py",
            "tests/test_api/test_member_notification_api.py",
        ],
    ),
    (
        "h5-component-behavior",
        REPO_ROOT / "frontend",
        [
            "pnpm",
            "--filter",
            "@yimatong/h5",
            "exec",
            "vitest",
            "run",
            "src/components/MemberJoinCard.test.tsx",
            "src/components/MemberCouponWallet.test.tsx",
            "src/components/MemberNotificationCenter.test.tsx",
            "src/components/ShopRedirect.test.tsx",
        ],
    ),
    (
        "browser-api-postgresql-journey",
        REPO_ROOT / "frontend",
        [
            "pnpm",
            "exec",
            "playwright",
            "test",
            "e2e/baseline-journey.spec.ts",
            "--config=playwright.member-repurchase.config.ts",
            "--project=chromium",
            "--grep=explicit consent",
        ],
    ),
)

ACCEPTANCE_FILES = (
    "test_baseline_rebuild.py",
    "test_brand_membership_rls.py",
    "test_repurchase_coupon_rls.py",
    "test_commerce_integration_rls.py",
    "test_repurchase_workbench_rls.py",
    "test_privacy_governance_rls.py",
)


def run_command(name: str, cwd: Path, command: list[str]) -> dict[str, object]:
    started = datetime.now(UTC)
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    output = (completed.stdout + "\n" + completed.stderr).strip()
    return {
        "name": name,
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": command,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "exit_code": completed.returncode,
        "output_tail": output[-12000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    internal = [run_command(name, cwd, command) for name, cwd, command in COMMANDS]
    for filename in ACCEPTANCE_FILES:
        internal.append(
            run_command(
                f"postgresql-{filename.removesuffix('.py')}",
                REPO_ROOT / "backend",
                ["uv", "run", "pytest", "-m", "acceptance", f"tests/test_acceptance/{filename}"],
            )
        )
    internal_status = "passed" if all(item["status"] == "passed" for item in internal) else "failed"
    overall_status = "failed" if internal_status == "failed" else "pending_external"
    report = {
        "schema_version": manifest["schema_version"],
        "dataset_version": manifest["dataset_version"],
        "business_chain": manifest["business_chain"],
        "generated_at": datetime.now(UTC).isoformat(),
        "internal_status": internal_status,
        "overall_status": overall_status,
        "internal_evidence": internal,
        "scenarios": manifest["scenarios"],
        "external_registry": manifest["external_registry"],
    }
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": overall_status, "report": str(output)}, ensure_ascii=False))
    return 0 if internal_status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
