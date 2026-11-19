"""
Type definitions for type checking purposes.
"""

from http.cookiejar import CookieJar
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    AsyncIterable,
    AsyncIterator,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

if TYPE_CHECKING:  # pragma: no cover
    from ._auth import Auth  # noqa: F401
    from ._config import Proxy, Timeout  # noqa: F401
    from ._models import Cookies, Headers, Request  # noqa: F401
    from ._urls import URL, QueryParams  # noqa: F401


PrimitiveData = Optional[Union[str, int, float, bool]]

URLTypes = Union["URL", str]

QueryParamTypes = Union[
    "QueryParams",
    Mapping[str, Union[PrimitiveData, Sequence[PrimitiveData]]],
    List[Tuple[str, PrimitiveData]],
    Tuple[Tuple[str, PrimitiveData], ...],
    str,
    bytes,
]

HeaderTypes = Union[
    "Headers",
    Mapping[str, str],
    Mapping[bytes, bytes],
    Sequence[Tuple[str, str]],
    Sequence[Tuple[bytes, bytes]],
]

CookieTypes = Union["Cookies", CookieJar, Dict[str, str], List[Tuple[str, str]]]

TimeoutTypes = Union[
    Optional[float],
    Tuple[Optional[float], Optional[float], Optional[float], Optional[float]],
    "Timeout",
]
ProxyTypes = Union["URL", str, "Proxy"]
CertTypes = Union[str, Tuple[str, str], Tuple[str, str, str]]

AuthTypes = Union[
    Tuple[Union[str, bytes], Union[str, bytes]],
    Callable[["Request"], "Request"],
    "Auth",
]

RequestContent = Union[str, bytes, Iterable[bytes], AsyncIterable[bytes]]
ResponseContent = Union[str, bytes, Iterable[bytes], AsyncIterable[bytes]]
ResponseExtensions = Mapping[str, Any]

RequestData = Mapping[str, Any]

FileContent = Union[IO[bytes], bytes, str]
FileTypes = Union[
    # file (or bytes)
    FileContent,
    # (filename, file (or bytes))
    Tuple[Optional[str], FileContent],
    # (filename, file (or bytes), content_type)
    Tuple[Optional[str], FileContent, Optional[str]],
    # (filename, file (or bytes), content_type, headers)
    Tuple[Optional[str], FileContent, Optional[str], Mapping[str, str]],
]
RequestFiles = Union[Mapping[str, FileTypes], Sequence[Tuple[str, FileTypes]]]

RequestExtensions = Mapping[str, Any]

__all__ = ["AsyncByteStream", "SyncByteStream"]


class SyncByteStream:
    """
    A byte stream, iterable in sync style, yielding chunks of bytes.

    Subclass this and override the `.__iter__()` method to implement a custom
    stream. For example, to build a stream that yields two chunks of bytes:

    ```python
    class DoubleStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"Hello, "
            yield b"world!"
    ```
    """

    def __iter__(self) -> Iterator[bytes]:
        """
        A byte-iterator, yielding chunks of bytes.
        """
        raise NotImplementedError(
            "The '__iter__' method must be implemented."
        )  # pragma: no cover
        yield b""  # pragma: no cover

    def close(self) -> None:
        """
        Subclasses can override this method to release any network resources
        after a request/response cycle is complete.
        """


class AsyncByteStream:
    """
    A byte stream, iterable in async style, yielding chunks of bytes.

    Subclass this and override the `.__aiter__()` method to implement a custom
    stream. For example, to build a stream that yields two chunks of bytes:

    ```python
    class DoubleStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"Hello, "
            yield b"world!"
    ```
    """

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """
        A byte-iterator, yielding chunks of bytes.
        """
        raise NotImplementedError(
            "The '__aiter__' method must be implemented."
        )  # pragma: no cover
        yield b""  # pragma: no cover

    async def aclose(self) -> None:
        """
        Subclasses can override this method to release any network resources
        after a request/response cycle is complete.
        """
