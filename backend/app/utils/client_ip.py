"""客户端 IP 提取工具（支持反向代理）"""

from fastapi import Request


def get_client_ip(request: Request) -> str:
    """获取真实客户端 IP。优先使用 X-Forwarded-For，回退到 request.client.host。"""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        # X-Forwarded-For 可能包含多个 IP，取第一个（最原始的客户端）
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
