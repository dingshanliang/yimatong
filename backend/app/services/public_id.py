"""public_id 生成算法：CSPRNG + Base62 + Luhn 校验位"""

import secrets

# Base62 字符集：排除易混淆字符 0/O、1/l/I
BASE62_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE = len(BASE62_CHARS)  # 54 chars
ID_LENGTH = 10  # 随机部分长度
CHECKSUM_LENGTH = 1  # Luhn 校验位


def _random_base62_char() -> str:
    n = secrets.randbelow(BASE)
    return BASE62_CHARS[n]


def _generate_random_part() -> str:
    return "".join(_random_base62_char() for _ in range(ID_LENGTH))


def _luhn_checksum(s: str) -> str:
    total = 0
    for i, ch in enumerate(s):
        val = BASE62_CHARS.index(ch)
        if i % 2 == 0:
            val *= 2
            if val >= BASE:
                val -= BASE - 1
        total += val
    remainder = total % BASE
    if remainder == 0:
        return BASE62_CHARS[0]
    return BASE62_CHARS[BASE - remainder]


def generate_public_id() -> str:
    random_part = _generate_random_part()
    checksum = _luhn_checksum(random_part)
    return random_part + checksum


def validate_public_id(pid: str) -> bool:
    if len(pid) != ID_LENGTH + CHECKSUM_LENGTH:
        return False
    if not all(ch in BASE62_CHARS for ch in pid):
        return False
    expected_checksum = _luhn_checksum(pid[:ID_LENGTH])
    return pid[ID_LENGTH] == expected_checksum
