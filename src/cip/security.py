"""Application-side ingress bounds, including chunked requests."""

from typing import Any

from starlette.responses import JSONResponse


class PayloadTooLarge(Exception):
    pass


class BodyLimit:
    def __init__(self, app: Any, limit: int = 600_000):
        self.app, self.limit = app, limit

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        error = JSONResponse(
            {"type": "about:blank", "title": "request_too_large", "status": 413},
            status_code=413,
            media_type="application/problem+json",
        )
        headers = dict(scope.get("headers", []))
        try:
            if int(headers.get(b"content-length", b"0")) > self.limit:
                await error(scope, receive, send)
                return
        except ValueError:
            await JSONResponse({"status": 400, "title": "invalid_content_length"}, status_code=400)(
                scope, receive, send
            )
            return
        total = 0

        async def bounded_receive() -> Any:
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.limit:
                raise PayloadTooLarge()
            return message

        try:
            await self.app(scope, bounded_receive, send)
        except PayloadTooLarge:
            await error(scope, receive, send)
