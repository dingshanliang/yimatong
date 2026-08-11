"""Request body limits for JSON-heavy connector mutations."""

from collections.abc import Awaitable, Callable

from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

CONNECTOR_JSON_BODY_LIMIT = 1_048_576
_MUTATION_METHODS = {"POST", "PUT", "PATCH"}


class ConnectorRequestBodyLimitMiddleware:
    """Bound connector JSON requests before Starlette materializes their bodies."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._applies(scope):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > CONNECTOR_JSON_BODY_LIMIT:
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
            if received > CONNECTOR_JSON_BODY_LIMIT:
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
    def _applies(scope: Scope) -> bool:
        if scope["type"] != "http" or scope.get("method") not in _MUTATION_METHODS:
            return False
        return str(scope.get("path", "")).startswith("/api/v1/connectors")

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"detail": "Request body too large"}, status_code=413)
        await response(scope, receive, send)
