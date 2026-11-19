"""
Regression tests for sharing a sync client between threads.

See #3566: `RuntimeError: deque mutated during iteration` (along with
`KeyError` and `LocalProtocolError` state corruption) when a `Client` with
`http2=True` is shared between threads.

HTTP/2 connections multiplex concurrent requests onto a single connection,
but the underlying `h2` state machine (including the HPACK encoder and
decoder) is not thread-safe. HTTP/1.1 connections already avoid this in
httpcore by rejecting concurrent requests with `ConnectionNotAvailable`, so
that the connection pool hands each in-flight request its own connection.
The sync transport restores the same invariant for HTTP/2 connections.
"""

import socket
import sys
import threading
import typing

import h2.config
import h2.connection
import h2.events
import h2.settings
import httpcore
import pytest

import httpx

RESPONSE_BODY = b"Hello, HTTP/2!"


class HTTP2Server:
    """
    A minimal cleartext (prior knowledge) HTTP/2 server.

    Speaks just enough HTTP/2 to serve a fixed 200 response to any request,
    advertising a high `MAX_CONCURRENT_STREAMS` value so that clients are
    willing to multiplex requests onto a single connection.
    """

    def __init__(self) -> None:
        self._running = True
        self._threads: list[threading.Thread] = []
        self._connections: list[socket.socket] = []
        self._listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listen_socket.bind(("127.0.0.1", 0))
        self._listen_socket.listen(128)
        self.port = self._listen_socket.getsockname()[1]
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    @property
    def url(self) -> httpx.URL:
        return httpx.URL(f"http://127.0.0.1:{self.port}/")

    def _accept_loop(self) -> None:
        while self._running:
            connection, _ = self._listen_socket.accept()
            if not self._running:  # pragma: no cover
                connection.close()
                break
            self._connections.append(connection)
            thread = threading.Thread(
                target=self._handle_connection, args=(connection,), daemon=True
            )
            self._threads.append(thread)
            thread.start()

    def _handle_connection(self, connection: socket.socket) -> None:
        state = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False)
        )
        state.local_settings = h2.settings.Settings(
            client=False,
            initial_values={
                h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS: 100,
                # A large dynamic table keeps many entries in the client's
                # HPACK encoder, widening the window for the mutation race.
                h2.settings.SettingCodes.HEADER_TABLE_SIZE: 65536,
            },
        )
        state.decoder.max_allowed_table_size = 65536
        state.initiate_connection()
        connection.sendall(state.data_to_send())
        try:
            while True:
                data = connection.recv(65535)
                if not data:
                    break
                for event in state.receive_data(data):
                    if isinstance(event, h2.events.RequestReceived):
                        state.send_headers(
                            event.stream_id,
                            [
                                (b":status", b"200"),
                                (b"content-type", b"text/plain"),
                                (b"content-length", str(len(RESPONSE_BODY)).encode()),
                            ],
                        )
                        state.send_data(event.stream_id, RESPONSE_BODY, end_stream=True)
                connection.sendall(state.data_to_send())
        except Exception:  # pragma: no cover
            pass
        finally:
            connection.close()

    def close(self) -> None:
        self._running = False
        wake_connection = socket.create_connection(("127.0.0.1", self.port))
        wake_connection.close()
        self._accept_thread.join()
        self._listen_socket.close()
        for connection in self._connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:  # pragma: no cover
                pass
            connection.close()
        for thread in self._threads:
            thread.join()


@pytest.fixture()
def http2_server() -> typing.Iterator[HTTP2Server]:
    server = HTTP2Server()
    try:
        yield server
    finally:
        server.close()


def test_client_shared_between_threads_with_http2(http2_server):
    """
    A sync client must be safe to share between threads when HTTP/2 is
    enabled. See #3566.
    """
    num_threads = 8
    rounds = 50
    barrier = threading.Barrier(num_threads)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def worker(worker_id: int) -> None:
        for request_id in range(rounds):
            # Synchronize every request, so that all threads enter the shared
            # HTTP/2 send path at the same time.
            barrier.wait()
            with errors_lock:
                if errors:  # pragma: no cover
                    # Another thread already failed. Keep the barrier in sync,
                    # but stop issuing requests.
                    continue
            try:
                headers = {
                    f"x-header-{index}": f"value-{worker_id}-{request_id}-{index}"
                    for index in range(20)
                }
                response = client.get(http2_server.url, headers=headers)
                assert response.status_code == 200
                assert response.content == RESPONSE_BODY
            except BaseException as exc:  # pragma: no cover
                with errors_lock:
                    errors.append(exc)

    # Use an aggressive thread switch interval, so that threads share the GIL
    # in the middle of encoding operations on the shared connection.
    switch_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.0001)
    try:
        with httpx.Client(http2=True, http1=False, timeout=10.0) as client:
            threads = [
                threading.Thread(target=worker, args=(worker_id,))
                for worker_id in range(num_threads)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
    finally:
        sys.setswitchinterval(switch_interval)

    assert errors == []


class DummyStream:
    def __init__(self) -> None:
        self.closed = False

    def __iter__(self) -> typing.Iterator[bytes]:
        yield b"chunk"

    def close(self) -> None:
        self.closed = True


class DummyConnection(httpcore.ConnectionInterface):
    def __init__(self) -> None:
        self.request_count = 0
        self.closed = False
        self.raise_error = False

    def handle_request(self, request: httpcore.Request) -> httpcore.Response:
        self.request_count += 1
        if self.raise_error:  # pragma: no cover
            raise ValueError("boom")
        return httpcore.Response(
            status=200,
            headers=[(b":status", b"200")],
            content=DummyStream(),
            extensions={},
        )

    def close(self) -> None:
        self.closed = True

    def info(self) -> str:
        return "dummy"

    def can_handle_request(self, origin: httpcore.Origin) -> bool:
        return True

    def is_available(self) -> bool:
        return True

    def has_expired(self) -> bool:
        return False

    def is_idle(self) -> bool:
        return True

    def is_closed(self) -> bool:
        return False


def test_single_flight_connection_is_exclusive() -> None:
    """
    A wrapped connection rejects requests while another request is in flight,
    and accepts them again once the response has been closed.
    """
    from httpx._transports.default import _SingleFlightConnection

    inner = DummyConnection()
    connection = _SingleFlightConnection(inner)
    request = httpcore.Request("GET", "http://example.com/")

    assert connection.can_handle_request(request.url.origin)
    assert connection.is_available()
    assert connection.is_idle()
    assert not connection.is_closed()
    assert not connection.has_expired()
    assert connection.info() == "dummy"

    response = connection.handle_request(request)
    assert not connection.is_available()
    assert isinstance(response.stream, typing.Iterable)
    assert list(response.stream) == [b"chunk"]

    with pytest.raises(httpcore.ConnectionNotAvailable):
        connection.handle_request(request)

    response.close()
    assert connection.is_available()

    connection.handle_request(request).close()
    assert inner.request_count == 2

    connection.close()
    assert inner.closed


def test_single_flight_connection_releases_on_error() -> None:
    """
    A connection is released for re-use if the request raises.
    """
    from httpx._transports.default import _SingleFlightConnection

    inner = DummyConnection()
    inner.raise_error = True
    connection = _SingleFlightConnection(inner)
    request = httpcore.Request("GET", "http://example.com/")

    with pytest.raises(ValueError):
        connection.handle_request(request)
    assert connection.is_available()

    inner.raise_error = False
    connection.handle_request(request).close()
    assert inner.request_count == 2
