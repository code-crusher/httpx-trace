"""
Unit tests for the public API docstrings.

Ensures that every member of the public API is documented, so that the
docstrings are consistently available for code autocompletion tools,
and for the API reference documentation.
"""

from __future__ import annotations

import inspect
import typing

import httpx


def public_api_members() -> list[tuple[str, typing.Any]]:
    """
    Return every module member, class, property, and method that is
    accessible from the public API.
    """
    items = []
    for name in httpx.__all__:
        obj = getattr(httpx, name)
        items.append((f"httpx.{name}", obj))

        if not inspect.isclass(obj):
            continue

        for klass in obj.__mro__:
            if not klass.__module__.startswith("httpx"):
                # Only include items defined within the httpx package,
                # rather than inherited from `object`, `Exception`, etc.
                continue
            for member_name, member in vars(klass).items():
                if member_name.startswith("_"):
                    continue
                if isinstance(member, (classmethod, staticmethod)):
                    member = member.__func__
                if isinstance(member, property):
                    items.append((f"httpx.{name}.{member_name}", member.fget))
                elif inspect.isfunction(member):
                    items.append((f"httpx.{name}.{member_name}", member))
    return items


def test_module_docstring() -> None:
    assert httpx.__doc__ is not None


def test_all_public_api_is_documented() -> None:
    """
    All public API classes, functions, properties, and methods include
    a docstring.
    """
    undocumented = [name for name, obj in public_api_members() if obj.__doc__ is None]

    if undocumented:  # pragma: no cover
        raise AssertionError(
            "Public API members without a docstring:\n" + "\n".join(undocumented)
        )
