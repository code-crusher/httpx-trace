import typing

import pytest

from tests import conftest


def test_server_disables_websockets(server: conftest.TestServer) -> None:
    """
    The test server only serves HTTP, so WebSockets support must be disabled.

    Otherwise uvicorn imports `websockets.legacy`, which is deprecated as of
    websockets 14.0 and raises a DeprecationWarning. Since our test suite turns
    warnings into errors, that killed the server thread and made the test suite
    hang. See https://github.com/encode/httpx/issues/3708
    """
    assert server.config.ws == "none"
    assert server.config.ws_protocol_class is None


def test_serve_in_thread_raises_if_server_fails_to_start() -> None:
    """
    A server thread that dies during startup must not hang the test suite.
    """

    class FailingServer:
        started = False
        should_exit = False

        def run(self) -> None:
            # Simulate a server thread that dies during startup without ever
            # setting `started`, e.g. because of an exception in a uvicorn
            # import triggered by a warning being turned into an error.
            pass  # pragma: nocover

    failing_server = typing.cast(conftest.TestServer, FailingServer())
    with pytest.raises(RuntimeError, match="Test server failed to start"):
        for _ in conftest.serve_in_thread(failing_server):
            pass  # pragma: nocover
