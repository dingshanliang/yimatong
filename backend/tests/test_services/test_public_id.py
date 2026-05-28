"""A4-002: public_id 生成算法测试"""

from collections import Counter

from app.services.public_id import generate_public_id, validate_public_id


class TestPublicIdGeneration:
    def test_public_id_length(self):
        pid = generate_public_id()
        assert len(pid) == 11

    def test_public_id_characters(self):
        pid = generate_public_id()
        charset = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
        # Last char is Luhn check digit, also from Base62
        for ch in pid:
            assert ch in charset

    def test_no_ambiguous_characters(self):
        """Base62 charset should exclude 0/O, 1/l/I confusion chars"""
        from app.services.public_id import BASE62_CHARS

        ambiguous = {"O", "I", "l"}
        for ch in ambiguous:
            assert ch not in BASE62_CHARS

    def test_10000_unique_ids(self):
        ids = {generate_public_id() for _ in range(10000)}
        assert len(ids) == 10000

    def test_uniform_distribution(self):
        """First character should have roughly uniform distribution"""
        ids = [generate_public_id() for _ in range(5000)]
        first_chars = Counter(pid[0] for pid in ids)
        # With ~59 chars, expect ~85 per char for 5000 samples
        # Check no char dominates (> 3x expected)
        expected_per_char = 5000 / len(set(first_chars.keys()))
        for char, count in first_chars.items():
            assert count < expected_per_char * 3, f"Char '{char}' appears {count} times, too many"

    def test_luhn_validation_valid(self):
        pid = generate_public_id()
        assert validate_public_id(pid) is True

    def test_luhn_validation_tampered(self):
        pid = generate_public_id()
        # Change one character
        tampered = pid[:-1] + ("0" if pid[-1] != "0" else "1")
        assert validate_public_id(tampered) is False

    def test_luhn_validation_wrong_length(self):
        assert validate_public_id("ABC") is False

    def test_uses_csprng_not_random(self):
        """Verify module uses secrets, not random"""
        import inspect

        import app.services.public_id as mod

        source = inspect.getsource(mod)
        assert "secrets" in source
        assert "import random" not in source
