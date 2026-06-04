"""代运营工作台服务层测试"""
from app.services.ops import build_next_action, build_readiness_summary


def test_build_readiness_summary_all_passed():
    status = {"brands": 2, "products": 1, "published_pages": 1, "activated_batches": 1}
    result = build_readiness_summary(status)
    assert result["ready"] is True
    assert result["passed_count"] == 4
    assert result["total_count"] == 4
    assert result["percent"] == 100
    assert result["missing_keys"] == []
    assert result["missing_labels"] == []


def test_build_readiness_summary_partial():
    status = {"brands": 1, "products": 0, "published_pages": 1, "activated_batches": 0}
    result = build_readiness_summary(status)
    assert result["ready"] is False
    assert result["passed_count"] == 2
    assert result["total_count"] == 4
    assert result["percent"] == 50
    assert "product_created" in result["missing_keys"]
    assert "code_batch_activated" in result["missing_keys"]


def test_build_next_action_overdue_first():
    readiness = {"ready": True, "missing_keys": []}
    task_summary = {"overdue": 2, "pending": 5, "in_progress": 1, "high_priority": 0}
    result = build_next_action("测试客户", readiness, task_summary)
    assert result["type"] == "overdue_task"
    assert "逾期" in result["label"]


def test_build_next_action_missing_config():
    readiness = {"ready": False, "missing_keys": ["brand_configured"]}
    task_summary = {"overdue": 0, "pending": 0, "in_progress": 0, "high_priority": 0}
    result = build_next_action("测试客户", readiness, task_summary)
    assert result["type"] == "brand_configured"
    assert result["href"] == "/brands"


def test_build_next_action_all_good():
    readiness = {"ready": True, "missing_keys": []}
    task_summary = {"overdue": 0, "pending": 0, "in_progress": 0, "high_priority": 0}
    result = build_next_action("测试客户", readiness, task_summary)
    assert result["type"] == "checklist"
