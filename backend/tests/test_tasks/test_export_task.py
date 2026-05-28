"""A4-005: 导出 CSV 逻辑单元测试"""

import io
import csv


def _generate_csv_content(items: list[dict]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["public_id", "status"])
    writer.writeheader()
    writer.writerows(items)
    return output.getvalue()


class TestGenerateCSVContent:
    def test_csv_has_header_and_rows(self):
        items = [
            {"public_id": "ABC12345678", "status": "created"},
            {"public_id": "DEF98765432", "status": "activated"},
        ]
        csv = _generate_csv_content(items)
        lines = csv.strip().split("\n")
        assert lines[0].strip() == "public_id,status"
        assert len(lines) == 3
        assert "ABC12345678" in lines[1]

    def test_empty_items(self):
        csv = _generate_csv_content([])
        lines = csv.strip().split("\n")
        assert len(lines) == 1
        assert "public_id" in lines[0]
