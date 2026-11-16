from __future__ import annotations

import typing

import httpcore
import pytest

import httpx

if typing.TYPE_CHECKING:  # pragma: no cover
    from conftest import TestServer


def test_httpcore_all_exceptions_mapped() -> None:
    """
    All exception classes exposed by HTTPCore are properly mapped to an HTTPX-specific
    exception class.
    """
    expected_mapped_httpcore_exceptions = {
        value.__name__
        for _, value in vars(httpcore).items()
        if isinstance(value, type)
        and issubclass(value, Exception)
        and value is not httpcore.ConnectionNotAvailable
    }

    httpx_exceptions = {
        value.__name__
        for _, value in vars(httpx).items()
        if isinstance(value, type) and issubclass(value, Exception)
    }

    unmapped_exceptions = expected_mapped_httpcore_exceptions - httpx_exceptions

    if unmapped_exceptions:  # pragma: no cover
        pytest.fail(f"Unmapped httpcore exceptions: {unmapped_exceptions}")


def test_httpcore_exception_mapping(server: TestServer) -> None:
    """
    HTTPCore exception mapping works as expected.
    """
    impossible_port = 123456
    with pytest.raises(httpx.ConnectError):
        httpx.get(server.url.copy_with(port=impossible_port))

    with pytest.raises(httpx.ReadTimeout):
        httpx.get(
            server.url.copy_with(path="/slow_response"),
            timeout=httpx.Timeout(5, read=0.01),
        )


def test_httpcore_ssl_exception_mapping(server: TestServer) -> None:
    """
    SSL errors raised by HTTPCore are mapped to `httpx.SSLError`.

    The test server only speaks plain HTTP, so requesting it over `https://`
    triggers a TLS handshake failure.
    """
    https_url = server.url.copy_with(scheme="https")

    with pytest.raises(httpx.SSLError) as exc_info:
        httpx.get(https_url)

    # `SSLError` is a subclass of `ConnectError`, so it's also a
    # `NetworkError`/`TransportError`/`RequestError`.
    assert isinstance(exc_info.value, httpx.ConnectError)
    assert isinstance(exc_info.value, httpx.NetworkError)
    assert isinstance(exc_info.value, httpx.RequestError)


@pytest.mark.anyio
async def test_async_httpcore_ssl_exception_mapping(server: TestServer) -> None:
    """
    SSL errors raised by HTTPCore are mapped to `httpx.SSLError` in async contexts.
    """
    https_url = server.url.copy_with(scheme="https")

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.SSLError) as exc_info:
            await client.get(https_url)

    assert isinstance(exc_info.value, httpx.ConnectError)
    assert isinstance(exc_info.value, httpx.NetworkError)
    assert isinstance(exc_info.value, httpx.RequestError)


def test_request_attribute() -> None:
    # Exception without request attribute
    exc = httpx.ReadTimeout("Read operation timed out")
    with pytest.raises(RuntimeError):
        exc.request  # noqa: B018

    # Exception with request attribute
    request = httpx.Request("GET", "https://www.example.com")
    exc = httpx.ReadTimeout("Read operation timed out", request=request)
    assert exc.request == request
