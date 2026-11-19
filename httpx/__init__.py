from .__version__ import __description__, __title__, __version__
from ._api import *
from ._auth import *
from ._client import *
from ._config import *
from ._content import *
from ._exceptions import *
from ._models import *
from ._status_codes import *
from ._transports import *
from ._types import *
from ._urls import *

try:
    from ._main import main
except ImportError:  # pragma: no cover

    def main() -> None:  # type: ignore
        import sys

        print(
            "The httpx command line client could not run because the required "
            "dependencies were not installed.\nMake sure you've installed "
            "everything with: pip install 'httpx[cli]'"
        )
        sys.exit(1)


__all__ = [
    # __version__.py
    "__description__",
    "__title__",
    "__version__",
    # _api.py
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "request",
    "stream",
    # _auth.py
    "Auth",
    "BasicAuth",
    "DigestAuth",
    "FunctionAuth",
    "NetRCAuth",
    # _client.py
    "USE_CLIENT_DEFAULT",
    "AsyncClient",
    "Client",
    # _config.py
    "Limits",
    "Proxy",
    "Timeout",
    "create_ssl_context",
    # _content.py
    "ByteStream",
    # _exceptions.py
    "CloseError",
    "ConnectError",
    "ConnectTimeout",
    "CookieConflict",
    "DecodingError",
    "HTTPError",
    "HTTPStatusError",
    "InvalidURL",
    "LocalProtocolError",
    "NetworkError",
    "PoolTimeout",
    "ProtocolError",
    "ProxyError",
    "ReadError",
    "ReadTimeout",
    "RemoteProtocolError",
    "RequestError",
    "RequestNotRead",
    "ResponseNotRead",
    "StreamClosed",
    "StreamConsumed",
    "StreamError",
    "TimeoutException",
    "TooManyRedirects",
    "TransportError",
    "UnsupportedProtocol",
    "WriteError",
    "WriteTimeout",
    # _models.py
    "Cookies",
    "Headers",
    "Request",
    "Response",
    # _status_codes.py
    "codes",
    # _transports.py
    "ASGITransport",
    "AsyncBaseTransport",
    "BaseTransport",
    "AsyncHTTPTransport",
    "HTTPTransport",
    "MockTransport",
    "WSGITransport",
    # _types.py
    "AsyncByteStream",
    "SyncByteStream",
    # _urls.py
    "URL",
    "QueryParams",
    # _main.py
    "main",
]


__locals = locals()
for __name in __all__:
    if not __name.startswith("__"):
        setattr(__locals[__name], "__module__", "httpx")  # noqa
