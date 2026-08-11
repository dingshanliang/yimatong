"""客户端 IP 提取工具（仅信任显式配置的反向代理）。"""

import hashlib
import hmac
import ipaddress
import os

from fastapi import Request

from app.core.config import settings


def get_client_ip(request: Request) -> str:
    """Return the first untrusted hop behind an explicitly trusted proxy.

    Forwarding headers from ordinary clients are ignored. Walking XFF from
    right to left prevents caller-prepended addresses from changing identity.
    """
    peer = request.client.host if request.client else "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
        trusted_networks = tuple(
            ipaddress.ip_network(value.strip(), strict=False)
            for value in settings.trusted_proxy_cidrs.split(",")
            if value.strip()
        )
    except ValueError:
        return peer
    if not any(peer_ip in network for network in trusted_networks):
        return peer

    forwarded = request.headers.get("X-Forwarded-For")
    if not forwarded:
        return peer
    try:
        hops = [ipaddress.ip_address(value.strip()) for value in forwarded.split(",") if value.strip()]
    except ValueError:
        return peer
    for hop in reversed(hops):
        if not any(hop in network for network in trusted_networks):
            return str(hop)
    return str(hops[0]) if hops else peer


def compute_ip_hash(client_ip: str) -> str | None:
    """计算客户端 IP 的加盐 HMAC-SHA256 哈希（用于匿名化存储）。

    使用可配置的 secret 加盐，防止彩虹表攻击和跨租户 IP 关联。
    当 IP 不可用时返回 None（不哈希哨兵值 "unknown"）。
    """
    if not client_ip or client_ip == "unknown":
        return None
    secret = getattr(settings, "ip_hash_secret", os.environ.get("IP_HASH_SECRET", ""))
    return hmac.new(secret.encode(), client_ip.encode(), hashlib.sha256).hexdigest()
