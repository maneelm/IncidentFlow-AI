from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class IngestionPayloadLimit:
    """Bound the raw body before JSON decoding, including requests without Content-Length."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        knowledge = (
            scope["type"] == "http"
            and scope["path"].startswith("/api/v1/runbooks")
            and scope["method"] in {"POST", "PATCH"}
        )
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") != "/api/v1/logs/ingest"
        ) and not knowledge:
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        max_bytes = 262144 if knowledge else self.max_bytes
        if headers.get(b"content-encoding", b"identity").lower() != b"identity":
            await self.reject(scope, receive, send, 415, "unsupported_encoding")
            return
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await self.reject(scope, receive, send, 400, "invalid_content_length")
            return
        if declared < 0:
            await self.reject(scope, receive, send, 400, "invalid_content_length")
            return
        if declared > max_bytes:
            await self.reject(scope, receive, send, 413, "payload_too_large")
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > max_bytes:
                await self.reject(scope, receive, send, 413, "payload_too_large")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        # Prevent parser recursion failures before Pydantic can enforce metadata depth.
        depth, quoted, escaped = 0, False, False
        json_body = b"multipart/form-data" not in headers.get(b"content-type", b"").lower()
        for byte in body if json_body else b"":
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (91, 123):
                depth += 1
                if depth > 16:
                    await self.reject(scope, receive, send, 422, "validation_error")
                    return
            elif byte in (93, 125):
                depth -= 1
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def reject(
        self, scope: Scope, receive: Receive, send: Send, status: int, code: str
    ) -> None:
        response = JSONResponse(
            {"error": {"code": code, "message": "Ingestion request rejected", "details": []}},
            status_code=status,
        )
        await response(scope, receive, send)
