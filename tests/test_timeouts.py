import gc
import typing
import warnings

import pytest

import httpx


@pytest.mark.anyio
async def test_read_timeout(server):
    timeout = httpx.Timeout(None, read=1e-6)

    async with httpx.AsyncClient(timeout=timeout) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.get(server.url.copy_with(path="/slow_response"))


@pytest.mark.anyio
async def test_write_timeout(server):
    timeout = httpx.Timeout(None, write=1e-6)

    async with httpx.AsyncClient(timeout=timeout) as client:
        with pytest.raises(httpx.WriteTimeout):
            data = b"*" * 1024 * 1024 * 100
            await client.put(server.url.copy_with(path="/slow_response"), content=data)


@pytest.mark.anyio
async def test_write_timeout_multipart(server):
    timeout = httpx.Timeout(None, write=1e-6)

    async with httpx.AsyncClient(timeout=timeout) as client:
        with pytest.raises(httpx.WriteTimeout):
            files = {"file": ("big.bin", b"*" * 1024 * 1024 * 100)}
            await client.put(server.url.copy_with(path="/slow_response"), files=files)


@pytest.mark.anyio
async def test_write_timeout_with_aiterator_content(server):
    # A write timeout while streaming an async iterator request body.
    # (Use a class-based iterable rather than an async generator, so that
    # only httpx's own stream handling is under test here.)
    class StreamingContent:
        def __init__(self, chunks: int, size: int) -> None:
            self._chunks_remaining = chunks
            self._size = size

        def __aiter__(self):
            return self

        async def __anext__(self) -> bytes:
            if not self._chunks_remaining:
                raise StopAsyncIteration
            self._chunks_remaining -= 1
            return b"*" * self._size

    timeout = httpx.Timeout(None, write=1e-6)

    async with httpx.AsyncClient(timeout=timeout) as client:
        with pytest.raises(httpx.WriteTimeout):
            await client.put(
                server.url.copy_with(path="/slow_response"),
                content=StreamingContent(chunks=10, size=1024 * 1024 * 10),
            )


@pytest.mark.anyio
async def test_request_stream_cancellation_does_not_leave_async_generator():
    # Regression test for https://github.com/encode/httpx/issues/3686
    #
    # When a request is cancelled part way through sending the request body,
    # we should not leave a suspended async generator to be later finalized
    # by the async framework. Trio issues a `ResourceWarning` for async
    # generators that are garbage collected before being exhausted.
    request = httpx.Request("PUT", "https://example.com", content=b"Hello, world!")
    stream = typing.cast(httpx.AsyncByteStream, request.stream)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        try:
            async for _ in stream:
                raise httpx.WriteTimeout("simulated cancellation")
        except httpx.WriteTimeout:
            pass

        for _ in range(3):
            gc.collect()

    assert not [
        warning for warning in caught if issubclass(warning.category, ResourceWarning)
    ]

    # Iterating a bytes request body remains repeatable, both from the
    # stream itself and from an already started iterator.
    assert b"".join([part async for part in stream]) == b"Hello, world!"
    iterator = stream.__aiter__()
    assert b"".join([part async for part in iterator]) == b"Hello, world!"
    assert b"".join([part async for part in iterator]) == b""


@pytest.mark.anyio
@pytest.mark.network
async def test_connect_timeout(server):
    timeout = httpx.Timeout(None, connect=1e-6)

    async with httpx.AsyncClient(timeout=timeout) as client:
        with pytest.raises(httpx.ConnectTimeout):
            # See https://stackoverflow.com/questions/100841/
            await client.get("http://10.255.255.1/")


@pytest.mark.anyio
async def test_pool_timeout(server):
    limits = httpx.Limits(max_connections=1)
    timeout = httpx.Timeout(None, pool=1e-4)

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        with pytest.raises(httpx.PoolTimeout):
            async with client.stream("GET", server.url):
                await client.get(server.url)


@pytest.mark.anyio
async def test_async_client_new_request_send_timeout(server):
    timeout = httpx.Timeout(1e-6)

    async with httpx.AsyncClient(timeout=timeout) as client:
        with pytest.raises(httpx.TimeoutException):
            await client.send(
                httpx.Request("GET", server.url.copy_with(path="/slow_response"))
            )
