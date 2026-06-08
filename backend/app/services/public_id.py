"""public_id 生成算法：CSPRNG + Base62 + Luhn 校验位"""

import secrets

# Base62 字符集：排除易混淆字符 0/O、1/l/I
BASE62_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE = len(BASE62_CHARS)  # 54 chars
_CHAR_TO_INDEX = {ch: i for i, ch in enumerate(BASE62_CHARS)}
_ID_LENGTH = 10  # 随机部分长度
_CHECKSUM_LENGTH = 1  # Luhn 校验位


def _random_base62_char() -> str:
    n = secrets.randbelow(_BASE)
    return BASE62_CHARS[n]


def _generate_random_part() -> str:
    return "".join(_random_base62_char() for _ in range(_ID_LENGTH))


def _luhn_checksum(s: str) -> str:
    total = 0
    for i, ch in enumerate(s):
        val = _CHAR_TO_INDEX[ch]
        if i % 2 == 0:
            val *= 2
            if val >= _BASE:
                val -= _BASE - 1
        total += val
    remainder = total % _BASE
    if remainder == 0:
        return BASE62_CHARS[0]
    return BASE62_CHARS[_BASE - remainder]


def generate_public_id() -> str:
    random_part = _generate_random_part()
    checksum = _luhn_checksum(random_part)
    return random_part + checksum


def validate_public_id(pid: str) -> bool:
    if len(pid) != _ID_LENGTH + _CHECKSUM_LENGTH:
        return False
    if not all(ch in BASE62_CHARS for ch in pid):
        return False
    expected_checksum = _luhn_checksum(pid[:_ID_LENGTH])
    return pid[_ID_LENGTH] == expected_checksum
