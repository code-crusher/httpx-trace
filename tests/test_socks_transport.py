"""
Unit tests for the SOCKS5 timeout wrapper used by the SOCKS proxy
transport, see `httpx/_transports/socks.py` and encode/httpx#3626.
"""

import ssl

import httpcore
import pytest

from httpx._transports.socks import (
    _UNSET,
    AsyncSOCKSProxy,
    AsyncTimeoutBackend,
    AsyncTimeoutStream,
    SOCKSProxy,
    SyncTimeoutBackend,
    SyncTimeoutStream,
    _resolve,
    _translate,
)


class FakeSyncStream:
    def __init__(self):
        self.read_calls = []
        self.write_calls = []
        self.start_tls_calls = []
        self.closed = False
        self.read_exception = None
        self.write_exception = None
        self.read_result = b"data"

    def read(self, max_bytes, timeout=None):
        self.read_calls.append((max_bytes, timeout))
        if self.read_exception is not None:
            raise self.read_exception
        return self.read_result

    def write(self, buffer, timeout=None):
        self.write_calls.append((buffer, timeout))
        if self.write_exception is not None:
            raise self.write_exception

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.start_tls_calls.append((ssl_context, server_hostname, timeout))
        return FakeSyncStream()

    def close(self):
        self.closed = True


class FakeAsyncStream(FakeSyncStream):
    def __init__(self):
        super().__init__()
        self.aclose_called = False

    async def read(self, max_bytes, timeout=None):
        self.read_calls.append((max_bytes, timeout))
        if self.read_exception is not None:
            raise self.read_exception
        return self.read_result

    async def write(self, buffer, timeout=None):
        self.write_calls.append((buffer, timeout))
        if self.write_exception is not None:
            raise self.write_exception

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.start_tls_calls.append((ssl_context, server_hostname, timeout))
        return FakeAsyncStream()

    async def aclose(self):
        self.aclose_called = True


class FakeSyncBackend:
    def __init__(self):
        self.connect_tcp_calls = []
        self.connect_unix_socket_calls = []
        self.sleep_calls = []
        self.stream = FakeSyncStream()

    def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        self.connect_tcp_calls.append(
            (host, port, timeout, local_address, socket_options)
        )
        return self.stream

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        self.connect_unix_socket_calls.append((path, timeout, socket_options))
        return self.stream

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)

    def extra_attribute(self):
        return "extra"


class FakeAsyncBackend:
    def __init__(self):
        self.connect_tcp_calls = []
        self.connect_unix_socket_calls = []
        self.sleep_calls = []
        self.stream = FakeAsyncStream()

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        self.connect_tcp_calls.append(
            (host, port, timeout, local_address, socket_options)
        )
        return self.stream

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        self.connect_unix_socket_calls.append((path, timeout, socket_options))
        return self.stream

    async def sleep(self, seconds):
        self.sleep_calls.append(seconds)

    def extra_attribute(self):
        return "extra"


def test_resolve():
    assert _resolve(_UNSET, 1.0) == 1.0
    assert _resolve(None, 1.0) is None
    assert _resolve(5.0, 1.0) == 5.0


def test_translate_passthrough_when_not_defaulted():
    exc = httpcore.ReadTimeout("too slow")
    assert _translate(exc, defaulted=False) is exc


def test_translate_converts_timeout_when_defaulted():
    exc = httpcore.ReadTimeout("too slow")
    result = _translate(exc, defaulted=True)
    assert isinstance(result, httpcore.ConnectTimeout)
    assert "too slow" in str(result)


def test_translate_converts_timeout_with_empty_message():
    result = _translate(httpcore.ReadTimeout(""), defaulted=True)
    assert isinstance(result, httpcore.ConnectTimeout)
    assert str(result) != ""


def test_translate_passthrough_for_non_timeout_when_defaulted():
    exc = httpcore.ConnectError("nope")
    assert _translate(exc, defaulted=True) is exc


def test_sync_stream_read_applies_default_timeout():
    stream = SyncTimeoutStream(FakeSyncStream(), default_timeout=3.0)
    assert stream.read(100) == b"data"
    assert stream._stream.read_calls == [(100, 3.0)]


def test_sync_stream_read_explicit_timeout_not_overridden():
    stream = SyncTimeoutStream(FakeSyncStream(), default_timeout=3.0)
    stream.read(100, timeout=7.0)
    assert stream._stream.read_calls == [(100, 7.0)]


def test_sync_stream_write_applies_default_timeout():
    stream = SyncTimeoutStream(FakeSyncStream(), default_timeout=3.0)
    stream.write(b"x")
    assert stream._stream.write_calls == [(b"x", 3.0)]


def test_sync_stream_write_explicit_timeout_not_overridden():
    stream = SyncTimeoutStream(FakeSyncStream(), default_timeout=3.0)
    stream.write(b"x", timeout=0)
    assert stream._stream.write_calls == [(b"x", 0)]


def test_sync_stream_read_timeout_translated_when_defaulted():
    fake = FakeSyncStream()
    fake.read_exception = httpcore.ReadTimeout("timed out")
    stream = SyncTimeoutStream(fake, default_timeout=3.0)
    with pytest.raises(httpcore.ConnectTimeout):
        stream.read(100)
    # The underlying stream is closed so a failed handshake doesn't leak the socket.
    assert fake.closed


def test_sync_stream_write_timeout_translated_when_defaulted():
    fake = FakeSyncStream()
    fake.write_exception = httpcore.WriteTimeout("timed out")
    stream = SyncTimeoutStream(fake, default_timeout=3.0)
    with pytest.raises(httpcore.ConnectTimeout):
        stream.write(b"x")
    assert fake.closed


def test_sync_stream_explicit_timeout_exception_not_translated():
    fake = FakeSyncStream()
    fake.read_exception = httpcore.ReadTimeout("timed out")
    stream = SyncTimeoutStream(fake, default_timeout=3.0)
    with pytest.raises(httpcore.ReadTimeout):
        stream.read(100, timeout=1.0)
    # Explicit timeout: the caller owns cleanup.
    assert not fake.closed


def test_sync_stream_non_timeout_exception_not_translated():
    fake = FakeSyncStream()
    fake.read_exception = httpcore.ConnectError("refused")
    stream = SyncTimeoutStream(fake, default_timeout=3.0)
    with pytest.raises(httpcore.ConnectError):
        stream.read(100)
    assert fake.closed


def test_sync_stream_close_failure_is_swallowed():
    class BadCloseStream(FakeSyncStream):
        def close(self):
            raise RuntimeError("boom")

    fake = BadCloseStream()
    fake.read_exception = httpcore.ReadTimeout("timed out")
    stream = SyncTimeoutStream(fake, default_timeout=3.0)
    with pytest.raises(httpcore.ConnectTimeout):
        stream.read(100)


def test_sync_stream_getattr_delegates():
    fake = FakeSyncStream()
    stream = SyncTimeoutStream(fake, default_timeout=3.0)
    assert stream.read_result == b"data"


def test_sync_stream_start_tls_wraps_result():
    stream = SyncTimeoutStream(FakeSyncStream(), default_timeout=3.0)
    ssl_context = ssl.create_default_context()
    wrapped = stream.start_tls(ssl_context, server_hostname="example.com")
    assert isinstance(wrapped, SyncTimeoutStream)
    assert wrapped._default_timeout == 3.0
    assert stream._stream.start_tls_calls == [(ssl_context, "example.com", 3.0)]


def test_sync_stream_start_tls_explicit_timeout():
    stream = SyncTimeoutStream(FakeSyncStream(), default_timeout=3.0)
    ssl_context = ssl.create_default_context()
    stream.start_tls(ssl_context, timeout=1.0)
    assert stream._stream.start_tls_calls == [(ssl_context, None, 1.0)]


def test_sync_stream_close_delegates():
    fake = FakeSyncStream()
    stream = SyncTimeoutStream(fake, default_timeout=3.0)
    stream.close()
    assert fake.closed


@pytest.mark.anyio
async def test_async_stream_read_applies_default_timeout():
    stream = AsyncTimeoutStream(FakeAsyncStream(), default_timeout=3.0)
    assert await stream.read(100) == b"data"
    assert stream._stream.read_calls == [(100, 3.0)]


@pytest.mark.anyio
async def test_async_stream_read_explicit_timeout_not_overridden():
    stream = AsyncTimeoutStream(FakeAsyncStream(), default_timeout=3.0)
    await stream.read(100, timeout=9.0)
    assert stream._stream.read_calls == [(100, 9.0)]


@pytest.mark.anyio
async def test_async_stream_write_applies_default_timeout():
    stream = AsyncTimeoutStream(FakeAsyncStream(), default_timeout=3.0)
    await stream.write(b"x")
    assert stream._stream.write_calls == [(b"x", 3.0)]


@pytest.mark.anyio
async def test_async_stream_write_explicit_timeout_not_overridden():
    stream = AsyncTimeoutStream(FakeAsyncStream(), default_timeout=3.0)
    await stream.write(b"x", timeout=9.0)
    assert stream._stream.write_calls == [(b"x", 9.0)]


@pytest.mark.anyio
async def test_async_stream_read_timeout_translated_when_defaulted():
    fake = FakeAsyncStream()
    fake.read_exception = httpcore.ReadTimeout("timed out")
    stream = AsyncTimeoutStream(fake, default_timeout=3.0)
    with pytest.raises(httpcore.ConnectTimeout):
        await stream.read(100)
    assert fake.aclose_called


@pytest.mark.anyio
async def test_async_stream_write_timeout_translated_when_defaulted():
    fake = FakeAsyncStream()
    fake.write_exception = httpcore.WriteTimeout("timed out")
    stream = AsyncTimeoutStream(fake, default_timeout=3.0)
    with pytest.raises(httpcore.ConnectTimeout):
        await stream.write(b"x")
    assert fake.aclose_called


@pytest.mark.anyio
async def test_async_stream_explicit_timeout_exception_not_translated():
    fake = FakeAsyncStream()
    fake.read_exception = httpcore.ReadTimeout("timed out")
    stream = AsyncTimeoutStream(fake, default_timeout=3.0)
    with pytest.raises(httpcore.ReadTimeout):
        await stream.read(100, timeout=1.0)
    assert not fake.aclose_called


@pytest.mark.anyio
async def test_async_stream_non_timeout_exception_not_translated():
    fake = FakeAsyncStream()
    fake.write_exception = httpcore.ConnectError("refused")
    stream = AsyncTimeoutStream(fake, default_timeout=3.0)
    with pytest.raises(httpcore.ConnectError):
        await stream.write(b"x")
    assert fake.aclose_called


@pytest.mark.anyio
async def test_async_stream_close_failure_is_swallowed():
    class BadCloseStream(FakeAsyncStream):
        async def aclose(self):
            raise RuntimeError("boom")

    fake = BadCloseStream()
    fake.read_exception = httpcore.ReadTimeout("timed out")
    stream = AsyncTimeoutStream(fake, default_timeout=3.0)
    with pytest.raises(httpcore.ConnectTimeout):
        await stream.read(100)


@pytest.mark.anyio
async def test_async_stream_getattr_delegates():
    stream = AsyncTimeoutStream(FakeAsyncStream(), default_timeout=3.0)
    assert stream.read_result == b"data"


@pytest.mark.anyio
async def test_async_stream_start_tls_wraps_result():
    stream = AsyncTimeoutStream(FakeAsyncStream(), default_timeout=3.0)
    ssl_context = ssl.create_default_context()
    wrapped = await stream.start_tls(ssl_context, server_hostname="example.com")
    assert isinstance(wrapped, AsyncTimeoutStream)
    assert wrapped._default_timeout == 3.0
    assert stream._stream.start_tls_calls == [(ssl_context, "example.com", 3.0)]


@pytest.mark.anyio
async def test_async_stream_start_tls_explicit_timeout():
    stream = AsyncTimeoutStream(FakeAsyncStream(), default_timeout=3.0)
    ssl_context = ssl.create_default_context()
    await stream.start_tls(ssl_context, timeout=1.0)
    assert stream._stream.start_tls_calls == [(ssl_context, None, 1.0)]


@pytest.mark.anyio
async def test_async_stream_aclose_delegates():
    fake = FakeAsyncStream()
    stream = AsyncTimeoutStream(fake, default_timeout=3.0)
    await stream.aclose()
    assert fake.aclose_called


def test_sync_backend_connect_tcp():
    backend = SyncTimeoutBackend(FakeSyncBackend())
    stream = backend.connect_tcp(
        "example.com", 443, timeout=2.0, local_address="127.0.0.1", socket_options=[]
    )
    assert isinstance(stream, SyncTimeoutStream)
    assert stream._default_timeout == 2.0
    assert backend._backend.connect_tcp_calls == [
        ("example.com", 443, 2.0, "127.0.0.1", [])
    ]


def test_sync_backend_connect_unix_socket():
    backend = SyncTimeoutBackend(FakeSyncBackend())
    stream = backend.connect_unix_socket("/tmp/sock", timeout=2.0, socket_options=[])
    assert isinstance(stream, SyncTimeoutStream)
    assert stream._default_timeout == 2.0
    assert backend._backend.connect_unix_socket_calls == [("/tmp/sock", 2.0, [])]


def test_sync_backend_sleep_delegates():
    backend = SyncTimeoutBackend(FakeSyncBackend())
    backend.sleep(1.5)
    assert backend._backend.sleep_calls == [1.5]


def test_sync_backend_getattr_delegates():
    backend = SyncTimeoutBackend(FakeSyncBackend())
    assert backend.extra_attribute() == "extra"


@pytest.mark.anyio
async def test_async_backend_connect_tcp():
    backend = AsyncTimeoutBackend(FakeAsyncBackend())
    stream = await backend.connect_tcp(
        "example.com", 443, timeout=2.0, local_address="127.0.0.1", socket_options=[]
    )
    assert isinstance(stream, AsyncTimeoutStream)
    assert stream._default_timeout == 2.0
    assert backend._backend.connect_tcp_calls == [
        ("example.com", 443, 2.0, "127.0.0.1", [])
    ]


@pytest.mark.anyio
async def test_async_backend_connect_unix_socket():
    backend = AsyncTimeoutBackend(FakeAsyncBackend())
    stream = await backend.connect_unix_socket("/tmp/sock", timeout=2.0)
    assert isinstance(stream, AsyncTimeoutStream)
    assert stream._default_timeout == 2.0
    assert backend._backend.connect_unix_socket_calls == [("/tmp/sock", 2.0, None)]


@pytest.mark.anyio
async def test_async_backend_sleep():
    backend = AsyncTimeoutBackend(FakeAsyncBackend())
    await backend.sleep(1.5)
    assert backend._backend.sleep_calls == [1.5]


@pytest.mark.anyio
async def test_async_backend_getattr_delegates():
    backend = AsyncTimeoutBackend(FakeAsyncBackend())
    assert backend.extra_attribute() == "extra"


def test_socks_proxy_pools_use_timeout_backend():
    for pool in (
        SOCKSProxy(proxy_url="socks5://localhost"),
        AsyncSOCKSProxy(proxy_url="socks5://localhost"),
    ):
        assert pool._network_backend.__class__ is (
            SyncTimeoutBackend if isinstance(pool, SOCKSProxy) else AsyncTimeoutBackend
        )


def test_socks_proxy_pools_preserve_wrapped_backend():
    pool = SOCKSProxy(proxy_url="socks5://localhost", network_backend=FakeSyncBackend())
    assert isinstance(pool._network_backend, SyncTimeoutBackend)
    assert isinstance(pool._network_backend._backend, FakeSyncBackend)


@pytest.mark.anyio
async def test_async_socks_proxy_pools_preserve_wrapped_backend():
    pool = AsyncSOCKSProxy(
        proxy_url="socks5://localhost", network_backend=FakeAsyncBackend()
    )
    assert isinstance(pool._network_backend, AsyncTimeoutBackend)
    assert isinstance(pool._network_backend._backend, FakeAsyncBackend)
