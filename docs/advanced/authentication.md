Authentication can either be included on a per-request basis...

```pycon
>>> auth = httpx.BasicAuth(username="username", password="secret")
>>> client = httpx.Client()
>>> response = client.get("https://www.example.com/", auth=auth)
```

Or configured on the client instance, ensuring that all outgoing requests will include authentication credentials...

```pycon
>>> auth = httpx.BasicAuth(username="username", password="secret")
>>> client = httpx.Client(auth=auth)
>>> response = client.get("https://www.example.com/")
```

## Basic authentication

HTTP basic authentication is an unencrypted authentication scheme that uses a simple encoding of the username and password in the request `Authorization` header. Since it is unencrypted it should typically only be used over `https`, although this is not strictly enforced.

```pycon
>>> auth = httpx.BasicAuth(username="finley", password="secret")
>>> client = httpx.Client(auth=auth)
>>> response = client.get("https://httpbin.org/basic-auth/finley/secret")
>>> response
<Response [200 OK]>
```

## Digest authentication

HTTP digest authentication is a challenge-response authentication scheme. Unlike basic authentication it provides encryption, and can be used over unencrypted `http` connections. It requires an additional round-trip in order to negotiate the authentication. 

```pycon
>>> auth = httpx.DigestAuth(username="olivia", password="secret")
>>> client = httpx.Client(auth=auth)
>>> response = client.get("https://httpbin.org/digest-auth/auth/olivia/secret")
>>> response
<Response [200 OK]>
>>> response.history
[<Response [401 UNAUTHORIZED]>]
```

## NetRC authentication

HTTPX can be configured to use [a `.netrc` config file](https://everything.curl.dev/usingcurl/netrc) for authentication.

The `.netrc` config file allows authentication credentials to be associated with specified hosts. When a request is made to a host that is found in the netrc file, the username and password will be included using HTTP basic authentication.

Example `.netrc` file:

```
machine example.org
login example-username
password example-password

machine python-httpx.org
login other-username
password other-password
```

Some examples of configuring `.netrc` authentication with `httpx`.

Use the default `.netrc` file in the users home directory:

```pycon
>>> auth = httpx.NetRCAuth()
>>> client = httpx.Client(auth=auth)
```

Use an explicit path to a `.netrc` file:

```pycon
>>> auth = httpx.NetRCAuth(file="/path/to/.netrc")
>>> client = httpx.Client(auth=auth)
```

Use the `NETRC` environment variable to configure a path to the `.netrc` file,
or fallback to the default.

```pycon
>>> auth = httpx.NetRCAuth(file=os.environ.get("NETRC"))
>>> client = httpx.Client(auth=auth)
```

The `NetRCAuth()` class uses [the `netrc.netrc()` function from the Python standard library](https://docs.python.org/3/library/netrc.html). See the documentation there for more details on exceptions that may be raised if the `.netrc` file is not found, or cannot be parsed.

## Custom authentication schemes

When issuing requests or instantiating a client, the `auth` argument can be used to pass an authentication scheme to use. The `auth` argument may be one of the following...

* A two-tuple of `username`/`password`, to be used with basic authentication.
* An instance of `httpx.BasicAuth()`, `httpx.DigestAuth()`, or `httpx.NetRCAuth()`.
* A callable, accepting a request and returning an authenticated request instance.
* An instance of subclasses of `httpx.Auth`.

The most involved of these is the last. A subclass of `httpx.Auth` should implement `def authenticate_request(request)`, which takes the request and returns the authenticated request to send. Most authentication schemes only need to override this method...

```python
class MyCustomAuth(httpx.Auth):
    def __init__(self, token):
        self.token = token

    def authenticate_request(self, request):
        # Send the request, with a custom `X-Authentication` header.
        request.headers['X-Authentication'] = self.token
        return request
```

Challenge-response authentication schemes, such as digest authentication, may also implement `def authenticate_response(response)`. This is called with the response to the request that was sent, and may return a new request to send. Returning `None` indicates that no further requests are required, and that the response should be returned to the caller.

```python
class MyCustomAuth(httpx.Auth):
    def __init__(self, token):
        self.token = token

    def authenticate_request(self, request):
        return request

    def authenticate_response(self, response):
        if response.status_code == 401:
            # If the server issues a 401 response then resend the request,
            # with a custom `X-Authentication` header.
            request = response.request
            request.headers['X-Authentication'] = self.token
            return request
        return None
```

Custom authentication classes are designed to not perform any I/O, so that they may be used with both sync and async client instances. If you are implementing an authentication scheme that requires the request body, then you need to indicate this on the class using a `requires_request_body` property.

You will then be able to access `request.content` inside the `.authenticate_request()` method.

```python
class MyCustomAuth(httpx.Auth):
    requires_request_body = True

    def __init__(self, token):
        self.token = token

    def authenticate_request(self, request):
        # Sign the request, based on `request.method`, `request.url`,
        # `request.headers`, and `request.content`.
        request.headers['X-Authentication'] = self.sign_request(request)
        return request

    def sign_request(self, request):
        ...
```

Similarly, if you are implementing a scheme that requires access to the response body, then use the `requires_response_body` property. You will then be able to access response body properties and methods such as `response.content`, `response.text`, `response.json()`, etc.

```python
class MyCustomAuth(httpx.Auth):
    requires_response_body = True

    def authenticate_response(self, response):
        data = response.json()
        ...
```

If you _do_ need to perform I/O other than HTTP requests, such as accessing a disk-based cache, or you need to use concurrency primitives, such as locks, then you should override `.async_authenticate_request()` and `.async_authenticate_response()` (in addition to the sync methods). The former pair will be used by `httpx.AsyncClient`, while the latter pair will be used by `httpx.Client`.

```python
import asyncio
import threading
import httpx


class MyCustomAuth(httpx.Auth):
    def __init__(self):
        self._sync_lock = threading.RLock()
        self._async_lock = asyncio.Lock()

    def sync_get_token(self):
        with self._sync_lock:
            ...

    def authenticate_request(self, request):
        token = self.sync_get_token()
        request.headers["Authorization"] = f"Token {token}"
        return request

    async def async_get_token(self):
        async with self._async_lock:
            ...

    async def async_authenticate_request(self, request):
        token = await self.async_get_token()
        request.headers["Authorization"] = f"Token {token}"
        return request
```

If you only want to support one of the two client types, then you should override the async method to raise an explicit `RuntimeError`.

```python
import httpx
import sync_only_library


class MyCustomAuth(httpx.Auth):
    def authenticate_request(self, request):
        token = sync_only_library.get_token(...)
        request.headers["Authorization"] = f"Token {token}"
        return request

    async def async_authenticate_request(self, request):
        raise RuntimeError("Cannot use a sync authentication class with httpx.AsyncClient")
```

Authentication schemes that need to make additional HTTP requests as part of the authentication exchange, such as issuing a request to refresh an access token, are not supported by the `Auth` API. For those cases you should use the [Transport API](transports.md) instead, which allows you to customise the entire request/response implementation.