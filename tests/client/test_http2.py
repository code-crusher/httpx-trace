"""
Tests for HTTP/2 robustness against server-side connection termination.

Servers may close HTTP/2 connections at any point; either gracefully with
a GOAWAY frame, or by closing connections that are sat idle in the
connection pool. In either case the client should be able to retry
idempotent requests on a new connection, rather than raising an error for
a request that was never processed.

See https://github.com/encode/httpx/issues/3324
"""

from __future__ import annotations

import socket
import threading
import typing

import httpcore
import pytest

import httpx

try:
    import h2.config  # noqa
    import h2.connection  # noqa
    import h2.events  # noqa
    import h2.exceptions  # noqa

    HTTP2_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTP2_AVAILABLE = False

requires_http2 = pytest.mark.skipif(
    not HTTP2_AVAILABLE, reason="HTTP/2 support requires the 'h2' package"
)


def run_h2_server(sock: socket.socket, script: str) -> None:
    """
    A minimal HTTP/2 server, running against a real TCP socket.

    Once the connection has been established the server responds to any
    request with a complete 200 response, unless a script is given...

    * "goaway": Send a graceful GOAWAY frame, error code 0, before
      responding to the first request. The connection is then left open,
      as a server performing a graceful shutdown would do.
    * "goaway_error": Send a GOAWAY frame with a non-zero error code,
      before responding to the first request.
    * "close_after_response": Send a complete response to the first
      request, and then close the connection, as if the server has an
      idle timeout that is shorter than the client's keep-alive expiry.
    """
    config = h2.config.H2Configuration(client_side=False)
    server = h2.connection.H2Connection(config=config)
    server.initiate_connection()
    sock.sendall(server.data_to_send())

    try:
        while True:
            data = sock.recv(65536)
            if not data:
                return

            try:
                events = server.receive_data(data)
            except h2.exceptions.ProtocolError:
                # Frames sent by the client after the connection has been
                # closed, such as a request body that is in flight when a
                # GOAWAY frame is sent, are simply ignored.
                return

            for event in events:
                if isinstance(event, h2.events.RequestReceived):
                    if script in ("goaway", "goaway_error"):
                        error_code = 0 if script == "goaway" else 1
                        server.close_connection(last_stream_id=0, error_code=error_code)
                    else:
                        server.send_headers(
                            event.stream_id,
                            [(b":status", b"200"), (b"content-length", b"2")],
                        )
                        server.send_data(event.stream_id, b"ok", end_stream=True)

            data_to_send = server.data_to_send()
            if data_to_send:
                sock.sendall(data_to_send)

            if script in ("goaway", "goaway_error"):
                # Leave the connection open. A graceful shutdown only
                # requires the server to stop accepting new streams.
                continue

            if script == "close_after_response":
                # Close the connection gracefully, with a FIN rather than
                # a RST, as a real server would.
                sock.shutdown(socket.SHUT_WR)
                try:
                    while sock.recv(65536):
                        pass  # pragma: no cover
                except OSError:
                    pass
                return
    except OSError:
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass


class H2ServerBackendMixin:
    """
    A scripted HTTP/2 server, listening on a real TCP socket.

    Each connection established by the client has a thread running
    `run_h2_server` on the other end, using the next script. Instances
    record which script was used for each connection.
    """

    def __init__(self, scripts: list[str]) -> None:
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(5)
        self._scripts = list(scripts)
        self.connection_scripts: list[str] = []

    def _accept(self) -> socket.socket:
        script = self._scripts.pop(0) if self._scripts else "respond"
        self.connection_scripts.append(script)

        client_sock = socket.create_connection(
            self._listener.getsockname()[:2], timeout=5.0
        )
        server_sock, _ = self._listener.accept()
        threading.Thread(
            target=run_h2_server, args=(server_sock, script), daemon=True
        ).start()
        return client_sock

    def close(self) -> None:
        self._listener.close()


SOCKET_OPTIONS = typing.Any


class H2ServerBackend(H2ServerBackendMixin, httpcore.NetworkBackend):
    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: SOCKET_OPTIONS = None,
    ) -> httpcore.NetworkStream:
        return H2SocketStream(self._accept())


class AsyncH2ServerBackend(H2ServerBackendMixin, httpcore.AsyncNetworkBackend):
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: SOCKET_OPTIONS = None,
    ) -> httpcore.AsyncNetworkStream:
        import anyio

        client_sock = await anyio.to_thread.run_sync(self._accept)
        return AsyncH2SocketStream(client_sock)


class H2SocketStream(httpcore.NetworkStream):
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        self._sock.settimeout(timeout)
        return self._sock.recv(max_bytes)

    def write(self, data: bytes, timeout: float | None = None) -> None:
        self._sock.settimeout(timeout)
        while data:
            sent = self._sock.send(data)
            data = data[sent:]

    def close(self) -> None:
        self._sock.close()

    def get_extra_info(self, info: str) -> typing.Any:
        return None


class AsyncH2SocketStream(httpcore.AsyncNetworkStream):
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        import anyio

        self._sock.settimeout(timeout)
        return await anyio.to_thread.run_sync(self._sock.recv, max_bytes)

    async def write(self, data: bytes, timeout: float | None = None) -> None:
        import anyio

        self._sock.settimeout(timeout)
        while data:
            sent = await anyio.to_thread.run_sync(self._sock.send, data)
            data = data[sent:]

    async def aclose(self) -> None:
        import anyio

        await anyio.to_thread.run_sync(self._sock.close)

    async def start_tls(  # pragma: no cover
        self,
        ssl_context: typing.Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise NotImplementedError()

    def get_extra_info(self, info: str) -> typing.Any:
        return None


def h2_client(backend: H2ServerBackend) -> httpx.Client:
    """
    A sync client, using a connection pool with our scripted HTTP/2 servers.
    """
    transport = httpx.HTTPTransport(http2=True)
    transport._pool = httpcore.ConnectionPool(
        network_backend=backend, http1=False, http2=True, keepalive_expiry=60.0
    )
    return httpx.Client(transport=transport, timeout=5.0)


def async_h2_client(backend: AsyncH2ServerBackend) -> httpx.AsyncClient:
    """
    An async client, using a connection pool with our scripted HTTP/2 servers.
    """
    transport = httpx.AsyncHTTPTransport(http2=True)
    transport._pool = httpcore.AsyncConnectionPool(
        network_backend=backend, http1=False, http2=True, keepalive_expiry=60.0
    )
    return httpx.AsyncClient(transport=transport, timeout=5.0)


@requires_http2
def test_http2_goaway_is_retried():
    # A graceful GOAWAY frame, sent before the server has responded to our
    # request, should be retried on a new connection.
    backend = H2ServerBackend(["goaway"])
    with h2_client(backend) as client:
        response = client.get("http://example.com/")

    assert response.status_code == 200
    assert response.text == "ok"
    assert response.http_version == "HTTP/2"
    # The failed connection, followed by a successful retry.
    assert backend.connection_scripts == ["goaway", "respond"]


@requires_http2
@pytest.mark.anyio
async def test_http2_goaway_is_retried_async():
    # A graceful GOAWAY frame, sent before the server has responded to our
    # request, should be retried on a new connection.
    backend = AsyncH2ServerBackend(["goaway"])
    async with async_h2_client(backend) as client:
        response = await client.get("http://example.com/")

    assert response.status_code == 200
    assert response.text == "ok"
    assert response.http_version == "HTTP/2"
    # The failed connection, followed by a successful retry.
    assert backend.connection_scripts == ["goaway", "respond"]


@requires_http2
def test_http2_goaway_with_error_code_is_not_retried():
    # A GOAWAY frame with a non-zero error code indicates a protocol error,
    # and should be raised rather than retried.
    backend = H2ServerBackend(["goaway_error", "respond"])
    with h2_client(backend) as client:
        with pytest.raises(httpx.RemoteProtocolError) as exc_info:
            client.get("http://example.com/")

    assert "ConnectionTerminated" in str(exc_info.value)
    # The server only ever saw a single connection.
    assert backend.connection_scripts == ["goaway_error"]


@requires_http2
@pytest.mark.anyio
async def test_http2_goaway_with_error_code_is_not_retried_async():
    # A GOAWAY frame with a non-zero error code indicates a protocol error,
    # and should be raised rather than retried.
    backend = AsyncH2ServerBackend(["goaway_error", "respond"])
    async with async_h2_client(backend) as client:
        with pytest.raises(httpx.RemoteProtocolError) as exc_info:
            await client.get("http://example.com/")

    assert "ConnectionTerminated" in str(exc_info.value)
    # The server only ever saw a single connection.
    assert backend.connection_scripts == ["goaway_error"]


@requires_http2
def test_http2_goaway_is_not_retried_for_non_idempotent_requests():
    # A POST request that could have side effects must not be retried,
    # even when the connection was gracefully terminated.
    backend = H2ServerBackend(["goaway", "respond"])
    with h2_client(backend) as client:
        with pytest.raises(httpx.RemoteProtocolError) as exc_info:
            client.post("http://example.com/", content=b"Hello, world!")

    assert "ConnectionTerminated" in str(exc_info.value)
    assert backend.connection_scripts == ["goaway"]


@requires_http2
@pytest.mark.anyio
async def test_http2_goaway_is_not_retried_for_non_idempotent_requests_async():
    # A POST request that could have side effects must not be retried,
    # even when the connection was gracefully terminated.
    backend = AsyncH2ServerBackend(["goaway", "respond"])
    async with async_h2_client(backend) as client:
        with pytest.raises(httpx.RemoteProtocolError) as exc_info:
            await client.post("http://example.com/", content=b"Hello, world!")

    assert "ConnectionTerminated" in str(exc_info.value)
    assert backend.connection_scripts == ["goaway"]


@requires_http2
def test_http2_goaway_is_not_retried_for_streaming_requests():
    # A request with a streaming body cannot be resent, so must not be
    # retried, even though it uses an idempotent method.
    backend = H2ServerBackend(["goaway", "respond"])
    with h2_client(backend) as client:
        with pytest.raises(httpx.RemoteProtocolError) as exc_info:
            client.request("GET", "http://example.com/", content=iter([b"Hello"]))

    assert "ConnectionTerminated" in str(exc_info.value)
    assert backend.connection_scripts == ["goaway"]


@requires_http2
def test_http2_goaway_is_only_retried_once():
    # If the retry also fails then the error should be raised, rather than
    # retrying indefinitely.
    backend = H2ServerBackend(["goaway", "goaway", "respond"])
    with h2_client(backend) as client:
        with pytest.raises(httpx.RemoteProtocolError) as exc_info:
            client.get("http://example.com/")

    assert "ConnectionTerminated" in str(exc_info.value)
    assert backend.connection_scripts == ["goaway", "goaway"]


@requires_http2
def test_http2_server_closing_idle_connection_is_retried():
    # A connection that was sat idle in the connection pool may have been
    # closed by the server, for example if the server has an idle timeout
    # that is shorter than the client's keep-alive expiry.
    # https://github.com/encode/httpx/issues/2983
    backend = H2ServerBackend(["close_after_response"])
    with h2_client(backend) as client:
        first = client.get("http://example.com/")
        second = client.get("http://example.com/")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.text == "ok"
    # The stale connection, followed by a successful retry.
    assert backend.connection_scripts == ["close_after_response", "respond"]


@requires_http2
@pytest.mark.anyio
async def test_http2_server_closing_idle_connection_is_retried_async():
    # A connection that was sat idle in the connection pool may have been
    # closed by the server, for example if the server has an idle timeout
    # that is shorter than the client's keep-alive expiry.
    # https://github.com/encode/httpx/issues/2983
    backend = AsyncH2ServerBackend(["close_after_response"])
    async with async_h2_client(backend) as client:
        first = await client.get("http://example.com/")
        second = await client.get("http://example.com/")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.text == "ok"
    # The stale connection, followed by a successful retry.
    assert backend.connection_scripts == ["close_after_response", "respond"]


@requires_http2
def test_connection_terminated_check():
    # Unit tests for the exception inspection used by the retry logic.
    import httpcore

    from httpx._transports.default import _connection_terminated

    # A graceful GOAWAY frame, error code 0, is retryable.
    goaway = h2.events.ConnectionTerminated()
    goaway.error_code = 0
    assert _connection_terminated(httpcore.RemoteProtocolError(goaway))
    # A GOAWAY frame with a non-zero error code indicates a protocol error.
    goaway = h2.events.ConnectionTerminated()
    goaway.error_code = 1
    assert not _connection_terminated(httpcore.RemoteProtocolError(goaway))
    # A connection that was sat idle in the pool being closed by the server.
    assert _connection_terminated(
        httpcore.RemoteProtocolError("Server disconnected without sending a response.")
    )
    # Any other protocol error is not retryable.
    assert not _connection_terminated(
        httpcore.RemoteProtocolError("Some other protocol error")
    )
    # Any other exception is not retryable.
    assert not _connection_terminated(httpcore.ReadError("Read error"))
    assert not _connection_terminated(None)
