import ssl
from pathlib import Path

import certifi
import pytest

import httpx
from httpx._transports.default import _create_default_ssl_context


@pytest.fixture(autouse=True)
def clear_default_ssl_context_cache():
    """
    Ensure that tests which inspect the cached default SSL context are
    isolated from each other, and from tests that mutate the environment.
    """
    _create_default_ssl_context.cache_clear()
    yield
    _create_default_ssl_context.cache_clear()


@pytest.mark.parametrize(
    "transport_class", [httpx.HTTPTransport, httpx.AsyncHTTPTransport]
)
def test_default_ssl_context_is_reused(transport_class):
    # The default SSL context is expensive to create, so it is built once and
    # shared between transports. See issue #3704.
    transport_1 = transport_class()
    transport_2 = transport_class()

    assert transport_1._pool._ssl_context is transport_2._pool._ssl_context


@pytest.mark.parametrize(
    "transport_class", [httpx.HTTPTransport, httpx.AsyncHTTPTransport]
)
@pytest.mark.parametrize(
    "kwargs",
    [
        {"verify": False},
        {"verify": ssl.create_default_context()},
        {"trust_env": False},
    ],
)
def test_non_default_ssl_context_is_not_shared(transport_class, kwargs):
    transport_1 = transport_class()
    transport_2 = transport_class(**kwargs)

    assert transport_1._pool._ssl_context is not transport_2._pool._ssl_context


@pytest.mark.parametrize(
    "transport_class", [httpx.HTTPTransport, httpx.AsyncHTTPTransport]
)
def test_default_ssl_context_differs_with_http2(transport_class):
    # `httpcore` sets the ALPN protocols on the SSL context when connecting,
    # so contexts for different protocol configurations must not be shared.
    transport_1 = transport_class(http2=False)
    transport_2 = transport_class(http2=True)

    assert transport_1._pool._ssl_context is not transport_2._pool._ssl_context


@pytest.mark.parametrize(
    "transport_class", [httpx.HTTPTransport, httpx.AsyncHTTPTransport]
)
@pytest.mark.parametrize(
    ("environment_variable", "environment_value"),
    [
        ("SSL_CERT_FILE", certifi.where()),
        ("SSL_CERT_DIR", str(Path(certifi.where()).parent)),
    ],
)
def test_default_ssl_context_responds_to_environment(
    transport_class, environment_variable, environment_value, monkeypatch
):
    # Environment variables are part of the cache key, so that changing them
    # results in a differently configured context.
    transport_1 = transport_class()
    context_1 = transport_1._pool._ssl_context

    monkeypatch.setenv(environment_variable, environment_value)
    transport_2 = transport_class()
    context_2 = transport_2._pool._ssl_context

    assert context_1 is not context_2


def test_default_ssl_context_created_once():
    # Creating many clients used to synchronously rebuild the SSL context for
    # each one, which blocked the event loop and serialized concurrent tasks.
    # The default context is now created at most once. See issue #3704.
    assert _create_default_ssl_context.cache_info().currsize == 0

    httpx.Client()
    httpx.Client()
    httpx.AsyncClient()
    httpx.AsyncClient()

    assert _create_default_ssl_context.cache_info().currsize == 1
