"""码包导出服务"""

import csv
import io
import uuid


def generate_csv_content(items: list[dict]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["public_id", "status"])
    writer.writeheader()
    writer.writerows(items)
    return output.getvalue()


async def enqueue_export_task(
    tenant_id: uuid.UUID, batch_id: uuid.UUID, operator_id: uuid.UUID
) -> str:
    task_id = f"export-{uuid.uuid4().hex[:12]}"
    # In production: enqueue to arq worker via Redis
    # For now: store task status in-memory placeholder
    _task_store[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "tenant_id": str(tenant_id),
        "batch_id": str(batch_id),
        "operator_id": str(operator_id),
    }
    return task_id


async def get_task_status(task_id: str) -> dict | None:
    return _task_store.get(task_id)


# Simple in-memory task store for development
_task_store: dict[str, dict] = {}
