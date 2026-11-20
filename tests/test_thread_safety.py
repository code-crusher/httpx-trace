"""
Thread-safety tests for clients that are shared between threads.

Regression tests for #3633: `RuntimeError: deque mutated during iteration`
(and related state corruption) when a synchronous client with HTTP/2 support
is shared across multiple threads.

The HTTP/2 state machine on a connection, including the HPACK encoder, is
not thread-safe. HTTP/1.1 connections already guarantee exclusive use in
httpcore by rejecting concurrent requests with `ConnectionNotAvailable`, so
the pool hands each in-flight request its own connection. HTTP/2 connections
accept concurrent requests, in order to multiplex streams over a single
connection, so the sync transport has to restore the same invariant itself.
"""

from __future__ import annotations

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
    A minimal HTTP/2 server, using prior-knowledge cleartext HTTP/2.

    Speaks just enough HTTP/2 to serve a fixed 200 response to any request,
    advertising enough concurrent streams that clients will multiplex
    requests over a single connection.
    """

    def __init__(self) -> None:
        self._running = True
        self._listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listen_socket.bind(("127.0.0.1", 0))
        self._listen_socket.listen(128)
        self.port = self._listen_socket.getsockname()[1]
        self._threads: list[threading.Thread] = []
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    @property
    def url(self) -> httpx.URL:
        return httpx.URL(f"http://127.0.0.1:{self.port}/")

    def _accept_loop(self) -> None:
        while self._running:
            try:
                connection, _ = self._listen_socket.accept()
            except OSError:  # pragma: no cover
                break
            if not self._running:
                connection.close()
                break
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
        try:
            connection.sendall(state.data_to_send())
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
        except OSError:  # pragma: no cover
            pass
        finally:
            connection.close()

    def close(self) -> None:
        self._running = False
        try:
            wake_connection = socket.create_connection(("127.0.0.1", self.port))
            wake_connection.close()
        except OSError:  # pragma: no cover
            pass
        self._accept_thread.join()
        self._listen_socket.close()
        for thread in self._threads:
            thread.join()


@pytest.fixture()
def http2_server():
    server = HTTP2Server()
    yield server
    server.close()


def test_sync_client_with_http2_is_thread_safe(http2_server):
    """
    A sync client with HTTP/2 enabled must be safe to share between threads.

    Requests from multiple threads are multiplexed onto shared HTTP/2
    connections, which must not corrupt the shared h2/hpack state.

    Regression test for #3633.
    """
    num_threads = 16
    rounds = 150
    barrier = threading.Barrier(num_threads)
    errors: list[Exception] = []

    def worker(worker_id: int) -> None:
        for request_id in range(rounds):
            # Synchronize every request, so that all threads enter the
            # shared HTTP/2 send path at the same time.
            barrier.wait()
            if errors:  # pragma: nocover
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
            except Exception as exc:  # pragma: nocover
                errors.append(exc)

    # Use an aggressive thread switch interval, so that threads share the
    # GIL in the middle of encoding operations on the shared connection.
    switch_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.0001)
    try:
        with httpx.Client(
            http2=True, http1=False, timeout=10.0, base_url=http2_server.url
        ) as client:
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


def test_sync_client_with_http2_still_multiplexes_within_a_thread(http2_server):
    """
    Exclusive use is only required while a request is in flight. Once the
    response has been read the connection is released, and is reused by
    subsequent requests, including from other threads.
    """
    with httpx.Client(
        http2=True, http1=False, timeout=10.0, base_url=http2_server.url
    ) as client:
        for _ in range(10):
            response = client.get("/")
            assert response.status_code == 200
            assert response.content == RESPONSE_BODY
            assert response.extensions["http_version"] == b"HTTP/2"


def test_sync_client_with_http2_reuses_connections_across_threads(http2_server):
    """
    When threads issue their requests one at a time they share a single
    connection, rather than each opening their own.
    """
    with httpx.Client(
        http2=True,
        http1=False,
        timeout=10.0,
        base_url=http2_server.url,
        limits=httpx.Limits(max_connections=1),
    ) as client:
        transport = typing.cast(httpx.HTTPTransport, client._transport)
        assert len(transport._pool.connections) == 0
        results: list[httpx.Response] = []

        def worker() -> None:
            response = client.get("/")
            with results_lock:
                results.append(response)

        results_lock = threading.Lock()
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
            thread.join()

        assert len(results) == 4
        assert all(r.status_code == 200 for r in results)
        assert len(transport._pool.connections) == 1


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
        if self.raise_error:
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
        return not self.closed

    def has_expired(self) -> bool:
        return False

    def is_idle(self) -> bool:
        return not self.closed

    def is_closed(self) -> bool:
        return self.closed


def get_single_flight_connection() -> tuple[DummyConnection, typing.Any]:
    from httpx._transports.default import SingleFlightConnection

    inner = DummyConnection()
    return inner, SingleFlightConnection(inner)


def test_single_flight_connection_is_exclusive() -> None:
    """
    A wrapped connection rejects requests while another request is in
    flight, and accepts them again once the response has been closed.
    """
    from httpx._transports.default import SingleFlightStream

    inner, connection = get_single_flight_connection()
    request = httpcore.Request("GET", "http://example.com/")

    assert connection.can_handle_request(request.url.origin)
    assert connection.is_available()
    assert connection.is_idle()
    assert not connection.is_closed()
    assert not connection.has_expired()
    assert connection.info() == "dummy"

    response = connection.handle_request(request)
    stream = typing.cast(SingleFlightStream, response.stream)
    assert not connection.is_available()
    assert not connection.is_idle()

    with pytest.raises(httpcore.ConnectionNotAvailable):
        connection.handle_request(request)

    assert list(stream) == [b"chunk"]
    assert not connection.is_available()

    stream.close()
    assert connection.is_available()
    assert connection.is_idle()

    connection.handle_request(request).close()
    assert inner.request_count == 2

    connection.close()
    assert inner.closed


def test_single_flight_connection_releases_on_error() -> None:
    """
    A connection is released for re-use if the request raises.
    """
    inner, connection = get_single_flight_connection()
    inner.raise_error = True
    request = httpcore.Request("GET", "http://example.com/")

    with pytest.raises(ValueError):
        connection.handle_request(request)
    assert connection.is_available()

    inner.raise_error = False
    connection.handle_request(request).close()
    assert inner.request_count == 2


def test_single_flight_connection_rejects_requests_once_closed() -> None:
    """
    Requests on a connection that has been closed are rejected with
    `ConnectionNotAvailable`, which the pool handles by retrying the
    request on a new connection.
    """
    inner, connection = get_single_flight_connection()
    request = httpcore.Request("GET", "http://example.com/")

    connection.close()
    assert inner.closed
    assert connection.is_closed()

    with pytest.raises(httpcore.ConnectionNotAvailable):
        connection.handle_request(request)


def test_single_flight_connection_defers_close_until_response_closed() -> None:
    """
    A close that is requested while a request is in flight is deferred
    until the response has been closed, so that the underlying HTTP/2
    state machine is never closed mid-request.
    """
    inner, connection = get_single_flight_connection()
    request = httpcore.Request("GET", "http://example.com/")

    response = connection.handle_request(request)
    connection.close()
    # The close is deferred while the request is in flight.
    assert not inner.closed
    assert not connection.is_available()

    # Requests are rejected rather than being sent on a closing connection.
    with pytest.raises(httpcore.ConnectionNotAvailable):
        connection.handle_request(request)

    response.close()
    assert inner.closed
    assert connection.is_closed()


def test_single_flight_connection_close_is_idempotent() -> None:
    inner, connection = get_single_flight_connection()

    connection.close()
    assert inner.closed
    connection.close()
    assert inner.closed
