"""A3-005: 存储服务单元测试"""


from app.services.storage import (
    MAX_FILE_SIZE,
    build_file_key,
    validate_file,
)


class TestValidateFile:
    def test_valid_png(self):
        assert validate_file("test.png", "image/png", 1024) is None

    def test_valid_jpg(self):
        assert validate_file("photo.jpg", "image/jpeg", 2048) is None

    def test_valid_webp(self):
        assert validate_file("image.webp", "image/webp", 4096) is None

    def test_invalid_extension(self):
        result = validate_file("doc.pdf", "application/pdf", 1024)
        assert result is not None
        assert "not allowed" in result.lower()

    def test_invalid_mime_type(self):
        result = validate_file("fake.png", "application/pdf", 1024)
        assert result is not None

    def test_file_too_large(self):
        result = validate_file("big.png", "image/png", MAX_FILE_SIZE + 1)
        assert result is not None
        assert "size" in result.lower()

    def test_file_at_max_size(self):
        assert validate_file("max.png", "image/png", MAX_FILE_SIZE) is None


class TestBuildFileKey:
    def test_key_format(self):
        key = build_file_key(
            tenant_id="019e6bce-0000-0000-0000-000000000001",
            module="brands",
            filename="logo.png",
        )
        assert key.startswith("019e6bce-0000-0000-0000-000000000001/brands/")
        assert key.endswith(".png")
        # UUID part in the middle
        parts = key.split("/")
        assert len(parts) == 3

    def test_different_filenames_same_uuid(self):
        key1 = build_file_key("t1", "brands", "a.png")
        key2 = build_file_key("t1", "brands", "b.png")
        assert key1 != key2
