from __future__ import annotations

import typing

from .._models import Request, Response
from .base import AsyncBaseTransport, BaseTransport

SyncHandler = typing.Callable[[Request], Response]
AsyncHandler = typing.Callable[[Request], typing.Coroutine[None, None, Response]]


__all__ = ["MockTransport"]


class MockTransport(AsyncBaseTransport, BaseTransport):
    """
    A transport that accepts a handler function, which can be used to
    map requests onto pre-determined responses, rather than making
    actual network requests.

    Usage:

    ```python
    def handler(request):
        return httpx.Response(200, json={"text": "Hello, world!"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    ```

    The handler function takes a `Request` instance, and either returns a
    `Response` instance, or, for the async case, returns a `Response`
    instance, optionally awaited from an async function.
    """

    def __init__(self, handler: SyncHandler | AsyncHandler) -> None:
        self.handler = handler

    def handle_request(
        self,
        request: Request,
    ) -> Response:
        """
        Send a single HTTP request, using the mock handler.
        """
        request.read()
        response = self.handler(request)
        if not isinstance(response, Response):  # pragma: no cover
            raise TypeError("Cannot use an async handler in a sync Client")
        return response

    async def handle_async_request(
        self,
        request: Request,
    ) -> Response:
        """
        Send a single HTTP request, using the mock handler.
        """
        await request.aread()
        response = self.handler(request)

        # Allow handler to *optionally* be an `async` function.
        # If it is, then the `response` variable need to be awaited to actually
        # return the result.

        if not isinstance(response, Response):
            response = await response

        return response
