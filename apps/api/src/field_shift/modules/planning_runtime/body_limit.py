"""Reject oversized or encoded planning requests before JSON parsing."""

from starlette.responses import JSONResponse


class PlanningBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(
            ("/api/planning-v1/", "/api/planning-workspace")
        ):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        if headers.get(b"content-encoding", b"identity") != b"identity":
            return await JSONResponse(
                {"detail": "Encoded planning bodies are unsupported."}, status_code=415
            )(scope, receive, send)
        messages = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > 8_000_000:
                return await JSONResponse(
                    {"detail": "Planning body exceeds 8 MB."}, status_code=413
                )(scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False):
                break
        index = 0

        async def replay():
            nonlocal index
            if index < len(messages):
                value = messages[index]
                index += 1
                return value
            return await receive()

        return await self.app(scope, replay, send)
