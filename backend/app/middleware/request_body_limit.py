"""Request body limits applied before Starlette materializes JSON payloads."""

from collections.abc import Awaitable, Callable

from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

CONNECTOR_JSON_BODY_LIMIT = 1_048_576
PUBLIC_CONSUMER_JSON_BODY_LIMIT = 16 * 1024
CHANNEL_JSON_BODY_LIMIT = 64 * 1024
RISK_DIVERSION_JSON_BODY_LIMIT = 64 * 1024
RISK_RULE_JSON_BODY_LIMIT = 64 * 1024
WECOM_CALLBACK_BODY_LIMIT = 64 * 1024
GMV_JSON_BODY_LIMIT = 256 * 1024
WEBHOOK_JSON_BODY_LIMIT = 64 * 1024
COMMERCE_EVENT_BODY_LIMIT = 64 * 1024
PILOT_JSON_BODY_LIMIT = 64 * 1024
_MUTATION_METHODS = {"POST", "PUT", "PATCH"}
_PUBLIC_CONSUMER_MUTATIONS = {
    "/api/v1/benefit-claims",
    "/api/v1/public/consents",
    "/api/v1/consumers/lead-capture",
    "/api/v1/consumers/membership/join",
    "/api/v1/consumers/membership/merge",
    "/api/v1/consumers/membership/recover",
    "/api/v1/consumers/membership/miniprogram-session",
    "/api/v1/consumers/membership/miniprogram-bind",
    "/api/v1/consumers/membership/privacy-requests",
    "/api/v1/consumers/membership/commerce-handoffs",
    "/api/v1/consumers/membership/notification-preferences/marketing-subscription",
    "/api/v1/consumers/membership/notification-preferences/service-wechat",
    "/api/v1/consumers/membership/notification-channel-grants",
    "/api/v1/commerce/handoffs/redeem",
    "/api/v1/public/leads",
}


def is_public_benefit_claim_path(path: str) -> bool:
    """Match only the registered PRD compatibility claim route shape."""

    parts = path.split("/")
    return (
        len(parts) == 7 and parts[1:5] == ["api", "v1", "public", "benefits"] and bool(parts[5]) and parts[6] == "claim"
    )


def is_benefit_claim_status_path(path: str) -> bool:
    """Match only the registered consumer claim status route shape."""

    parts = path.split("/")
    return len(parts) == 6 and parts[1:4] == ["api", "v1", "benefit-claims"] and bool(parts[4]) and parts[5] == "status"


def is_wecom_callback_path(path: str) -> bool:
    """Match only the registered WeCom callback route shape."""

    parts = path.split("/")
    return len(parts) == 7 and parts[1:6] == ["api", "v1", "integrations", "wecom", "callback"] and bool(parts[6])


class ConnectorRequestBodyLimitMiddleware:
    """Bound selected JSON mutations before Starlette materializes their bodies."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = self._limit_for_scope(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > limit:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass

        body_parts: list[bytes] = []
        received = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > limit:
                await self._reject(scope, receive, send)
                return
            body_parts.append(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": b"".join(body_parts), "more_body": False}

        await self.app(scope, replay_receive, send)

    @staticmethod
    def _limit_for_scope(scope: Scope) -> int | None:
        if scope["type"] != "http" or scope.get("method") not in _MUTATION_METHODS:
            return None
        path = str(scope.get("path", ""))
        if path.startswith("/api/v1/connectors"):
            return CONNECTOR_JSON_BODY_LIMIT
        if path == "/api/v1/webhooks/endpoints" or path.startswith("/api/v1/webhooks/endpoints/"):
            return WEBHOOK_JSON_BODY_LIMIT
        if path in {
            "/api/v1/commerce/events",
            "/api/v1/commerce/coupons/eligible",
            "/api/v1/commerce/coupons/transitions",
        }:
            return COMMERCE_EVENT_BODY_LIMIT
        if path.startswith("/api/v1/retrospectives/"):
            return PILOT_JSON_BODY_LIMIT
        if path.startswith("/api/v1/platform/tenants/") and path.endswith("/pilot-milestones/corrections"):
            return PILOT_JSON_BODY_LIMIT
        if path == "/api/v1/gmv/orders/import" or path.startswith("/api/v1/gmv/orders/"):
            return GMV_JSON_BODY_LIMIT
        if is_wecom_callback_path(path):
            return WECOM_CALLBACK_BODY_LIMIT
        if path.startswith("/api/v1/channels/"):
            return CHANNEL_JSON_BODY_LIMIT
        if (
            path == "/api/v1/risk-rules"
            or path.startswith("/api/v1/risk-rules/")
            or path == "/api/v1/risk/rules"
            or path.startswith("/api/v1/risk/rules/")
            or path == "/api/v1/risk/evaluate"
            or path.startswith("/api/v1/risk-alerts/")
            or path == "/api/v1/risk-notifications/mark-all-read"
            or path.startswith("/api/v1/risk-notifications/")
        ):
            return RISK_RULE_JSON_BODY_LIMIT
        if scope.get("method") == "POST" and ConnectorRequestBodyLimitMiddleware._is_diversion_mutation(path):
            return RISK_DIVERSION_JSON_BODY_LIMIT
        if (
            path in _PUBLIC_CONSUMER_MUTATIONS
            or is_public_benefit_claim_path(path)
            or (path.startswith("/api/v1/public/consents/") and path.endswith("/withdraw"))
        ):
            return PUBLIC_CONSUMER_JSON_BODY_LIMIT
        return None

    @staticmethod
    def _is_diversion_mutation(path: str) -> bool:
        parts = path.split("/")
        return (
            len(parts) == 7
            and parts[1:5] == ["api", "v1", "risk-dashboard", "diversion-clues"]
            and bool(parts[5])
            and parts[6] in {"evidence", "transition"}
        )

    @staticmethod
    def _is_wecom_callback(path: str) -> bool:
        """Match the registered callback shape, including invalid connector UUIDs."""

        return is_wecom_callback_path(path)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"detail": "Request body too large"}, status_code=413)
        await response(scope, receive, send)
