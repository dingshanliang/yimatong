"""客户端 IP 提取工具（支持反向代理）

优先级：X-Real-IP > X-Forwarded-For 首个 > request.client.host

注意：生产环境需在 Nginx 配置 real_ip 模块：
  set_real_ip_from 10.0.0.0/8;
  real_ip_header X-Forwarded-For;
"""

import hashlib
import hmac
import os

from fastapi import Request

from app.core.config import settings


def get_client_ip(request: Request) -> str:
    """获取真实客户端 IP。

    优先使用 Nginx 设置的 X-Real-IP（不可被客户端伪造），
    回退到 X-Forwarded-For 第一个 IP，最后回退到直连 IP。
    """
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


def compute_ip_hash(client_ip: str) -> str | None:
    """计算客户端 IP 的加盐 HMAC-SHA256 哈希（用于匿名化存储）。

    使用可配置的 secret 加盐，防止彩虹表攻击和跨租户 IP 关联。
    当 IP 不可用时返回 None（不哈希哨兵值 "unknown"）。
    """
    if not client_ip or client_ip == "unknown":
        return None
    secret = getattr(settings, "ip_hash_secret", os.environ.get("IP_HASH_SECRET", ""))
    return hmac.new(secret.encode(), client_ip.encode(), hashlib.sha256).hexdigest()
