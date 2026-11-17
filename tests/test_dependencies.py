import tomllib
from pathlib import Path

import httpx

import httpcore


def test_httpx_requires_python_314_compatible_httpcore() -> None:
    """
    httpx should only accept httpcore versions that import cleanly
    on Python 3.14. httpcore 1.0.7 and earlier raise `AttributeError`
    at import time on Python 3.14 when attempting to `setattr()` on the
    `SOCKET_OPTION` typing alias. httpcore 1.0.8 is the first release
    containing the fix. See:
    https://github.com/encode/httpcore/pull/1005
    https://github.com/encode/httpx/issues/3644
    """
    pyproject = Path(httpx.__file__).parent.parent / "pyproject.toml"
    with open(pyproject, "rb") as f:
        dependencies = tomllib.load(f)["project"]["dependencies"]

    (spec,) = [dep for dep in dependencies if dep.startswith("httpcore")]
    assert "1.0.8" in spec

    required = tuple(int(part) for part in spec.split(">=")[1].split("."))
    installed = tuple(
        int(part) for part in httpcore.__version__.split(".")[: len(required)]
    )
    assert installed >= required, (
        f"httpx requires httpcore{'.'.join(map(str, required))} or newer, "
        f"but httpcore{'.'.join(map(str, installed))} is installed."
    )
