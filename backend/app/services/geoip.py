"""GeoIP 地理位置解析服务

支持 MaxMind GeoLite2 数据库，降级到 CIDR 映射表。
数据库文件路径：环境变量 GEOLITE2_DB 或默认 data/GeoLite2-City.mmdb
"""

import ipaddress
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_reader = None


def _init_reader():
    global _reader
    if _reader is not None:
        return True
    db_path = os.environ.get(
        "GEOLITE2_DB",
        str(Path(__file__).resolve().parent.parent.parent / "data" / "GeoLite2-City.mmdb"),
    )
    try:
        import geoip2.database

        _reader = geoip2.database.Reader(db_path)
        return True
    except ImportError:
        logger.info("geoip2 library not installed, using CIDR fallback")
    except FileNotFoundError:
        logger.info("GeoLite2 database not found at %s, using CIDR fallback", db_path)
    except Exception as e:
        logger.warning("GeoIP init failed: %s, using CIDR fallback", e)
    return False


# CIDR 降级映射表
_FALLBACK_MAP: dict[str, str] = {
    "110.0.0.0/8": "北京",
    "112.0.0.0/8": "北京",
    "120.0.0.0/8": "上海",
    "121.0.0.0/8": "上海",
    "113.0.0.0/8": "广东",
    "119.0.0.0/8": "广东",
    "114.0.0.0/8": "湖北",
    "202.0.0.0/8": "四川",
    "221.0.0.0/8": "辽宁",
}


def resolve_ip_to_city(ip: str) -> str | None:
    """将 IP 地址解析为城市名称"""
    # 尝试 MaxMind GeoLite2
    if _init_reader() and _reader:
        try:
            resp = _reader.city(ip)
            city = resp.city.names.get("zh-CN") or resp.city.name
            if city:
                return city
        except Exception:
            pass

    # 降级到 CIDR 映射
    return _fallback_lookup(ip)


def _fallback_lookup(ip: str) -> str | None:
    try:
        addr = ipaddress.ip_address(ip)
        for cidr, city in _FALLBACK_MAP.items():
            if addr in ipaddress.ip_network(cidr):
                return city
    except ValueError:
        pass
    return None
