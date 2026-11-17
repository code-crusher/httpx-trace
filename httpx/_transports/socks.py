"""
SOCKS5 proxy pools that apply the request's connect timeout to the
SOCKS5 handshake itself.

httpcore's `httpcore.SOCKSProxy` applies the request's ``connect``
timeout when opening the TCP connection to the proxy, but the SOCKS5
negotiation that follows (`_init_socks5_connection()`) reads and writes
from the socket without passing any timeout. A SOCKS proxy that accepts
the TCP connection but never responds will therefore block the client
indefinitely. See encode/httpx#3626.

Rather than reimplementing the SOCKS5 state machine, this module wraps
the network backend that httpcore's SOCKS5 pool connects through. The
wrapper applies the connect timeout to any read/write that doesn't
carry an explicit timeout, which in practice is only the SOCKS5
handshake. Reads and writes issued by the HTTP layer always pass an
explicit timeout and are left alone.

Timeouts that trigger while the fallback is in effect are reported as
`httpcore.ConnectTimeout`, matching how httpcore classifies timeouts
raised while the connection is being established, and the underlying
socket is closed so a failed handshake does not leak it.
"""

from __future__ import annotations

import ssl
import typing

import httpcore

SOCKET_OPTION = httpcore.SOCKET_OPTION

# Sentinel for "argument not provided", so that an explicit `timeout=None`
# (meaning "no timeout") is distinguished from an omitted timeout.
_UNSET: typing.Any = object()


def _resolve(timeout: typing.Any, default: float | None) -> float | None:
    if timeout is _UNSET:
        return default
    return typing.cast("float | None", timeout)


def _translate(exc: Exception, defaulted: bool) -> Exception:
    if defaulted and isinstance(exc, httpcore.TimeoutException):
        return httpcore.ConnectTimeout(str(exc) or "SOCKS proxy handshake timed out")
    return exc


class SyncTimeoutStream(httpcore.NetworkStream):
    """
    Wraps a synchronous network stream, applying `default_timeout` to any
    `read()`/`write()` that doesn't specify a timeout of its own.
    """

    def __init__(
        self, stream: httpcore.NetworkStream, default_timeout: float | None
    ) -> None:
        self._stream = stream
        self._default_timeout = default_timeout

    def __getattr__(self, name: str) -> typing.Any:
        # Delegate any other attributes (e.g. `get_extra_info()`) to the
        # wrapped stream.
        return getattr(self._stream, name)

    def _on_failure(self, exc: Exception, defaulted: bool) -> Exception:
        if defaulted:
            try:
                self._stream.close()
            except Exception:
                pass
        return _translate(exc, defaulted)

    def read(self, max_bytes: int, timeout: typing.Any = _UNSET) -> bytes:
        defaulted = timeout is _UNSET
        try:
            return self._stream.read(
                max_bytes, timeout=_resolve(timeout, self._default_timeout)
            )
        except Exception as exc:
            raise self._on_failure(exc, defaulted) from exc

    def write(self, buffer: bytes, timeout: typing.Any = _UNSET) -> None:
        defaulted = timeout is _UNSET
        try:
            self._stream.write(buffer, timeout=_resolve(timeout, self._default_timeout))
        except Exception as exc:
            raise self._on_failure(exc, defaulted) from exc

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: typing.Any = _UNSET,
    ) -> SyncTimeoutStream:
        stream = self._stream.start_tls(
            ssl_context,
            server_hostname=server_hostname,
            timeout=_resolve(timeout, self._default_timeout),
        )
        return SyncTimeoutStream(stream, self._default_timeout)

    def close(self) -> None:
        self._stream.close()


class AsyncTimeoutStream(httpcore.AsyncNetworkStream):
    """
    Wraps an asynchronous network stream, applying `default_timeout` to
    any `read()`/`write()` that doesn't specify a timeout of its own.
    """

    def __init__(
        self, stream: httpcore.AsyncNetworkStream, default_timeout: float | None
    ) -> None:
        self._stream = stream
        self._default_timeout = default_timeout

    def __getattr__(self, name: str) -> typing.Any:
        # Delegate any other attributes (e.g. `get_extra_info()`) to the
        # wrapped stream.
        return getattr(self._stream, name)

    async def _on_failure(self, exc: Exception, defaulted: bool) -> Exception:
        if defaulted:
            try:
                await self._stream.aclose()
            except Exception:
                pass
        return _translate(exc, defaulted)

    async def read(self, max_bytes: int, timeout: typing.Any = _UNSET) -> bytes:
        defaulted = timeout is _UNSET
        try:
            return await self._stream.read(
                max_bytes, timeout=_resolve(timeout, self._default_timeout)
            )
        except Exception as exc:
            raise await self._on_failure(exc, defaulted) from exc

    async def write(self, buffer: bytes, timeout: typing.Any = _UNSET) -> None:
        defaulted = timeout is _UNSET
        try:
            await self._stream.write(
                buffer, timeout=_resolve(timeout, self._default_timeout)
            )
        except Exception as exc:
            raise await self._on_failure(exc, defaulted) from exc

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: typing.Any = _UNSET,
    ) -> AsyncTimeoutStream:
        stream = await self._stream.start_tls(
            ssl_context,
            server_hostname=server_hostname,
            timeout=_resolve(timeout, self._default_timeout),
        )
        return AsyncTimeoutStream(stream, self._default_timeout)

    async def aclose(self) -> None:
        await self._stream.aclose()


class SyncTimeoutBackend(httpcore.NetworkBackend):
    def __init__(self, backend: httpcore.NetworkBackend) -> None:
        self._backend = backend

    def __getattr__(self, name: str) -> typing.Any:
        # Delegate any other attributes to the wrapped backend.
        return getattr(self._backend, name)

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[SOCKET_OPTION] | None = None,
    ) -> SyncTimeoutStream:
        stream = self._backend.connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        return SyncTimeoutStream(stream, timeout)

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: typing.Iterable[SOCKET_OPTION] | None = None,
    ) -> SyncTimeoutStream:
        stream = self._backend.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )
        return SyncTimeoutStream(stream, timeout)

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


class AsyncTimeoutBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, backend: httpcore.AsyncNetworkBackend) -> None:
        self._backend = backend

    def __getattr__(self, name: str) -> typing.Any:
        # Delegate any other attributes to the wrapped backend.
        return getattr(self._backend, name)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncTimeoutStream:
        stream = await self._backend.connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        return AsyncTimeoutStream(stream, timeout)

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: typing.Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncTimeoutStream:
        stream = await self._backend.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )
        return AsyncTimeoutStream(stream, timeout)

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class SOCKSProxy(httpcore.SOCKSProxy):
    """
    `httpcore.SOCKSProxy` with the request's connect timeout applied to
    the SOCKS5 handshake, instead of only to the initial TCP connect.
    """

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        self._network_backend = SyncTimeoutBackend(self._network_backend)


class AsyncSOCKSProxy(httpcore.AsyncSOCKSProxy):
    """
    `httpcore.AsyncSOCKSProxy` with the request's connect timeout applied
    to the SOCKS5 handshake, instead of only to the initial TCP connect.
    """

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        self._network_backend = AsyncTimeoutBackend(self._network_backend)


__all__ = ["AsyncSOCKSProxy", "SOCKSProxy"]
