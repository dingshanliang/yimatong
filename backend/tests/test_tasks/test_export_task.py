"""A4-005: 导出任务逻辑单元测试"""


from app.services.code_export import generate_csv_content


class TestGenerateCSVContent:
    def test_csv_has_header_and_rows(self):
        items = [
            {"public_id": "ABC12345678", "status": "created"},
            {"public_id": "DEF98765432", "status": "activated"},
        ]
        csv = generate_csv_content(items)
        lines = csv.strip().split("\n")
        assert lines[0].strip() == "public_id,status"
        assert len(lines) == 3  # header + 2 rows
        assert "ABC12345678" in lines[1]

    def test_empty_items(self):
        csv = generate_csv_content([])
        lines = csv.strip().split("\n")
        assert len(lines) == 1  # only header
        assert "public_id" in lines[0]
