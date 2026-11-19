import ast
import importlib
import pathlib
import re

import httpx


def test_all_imports_are_exported() -> None:
    # All public module members must be listed in `__all__`.
    included_private_members = ["__description__", "__title__", "__version__"]
    assert sorted(httpx.__all__) == sorted(
        (
            member
            for member in vars(httpx).keys()
            if not member.startswith("_") or member in included_private_members
        ),
    )


def test_module_attribute() -> None:
    # Class and function members of the `httpx` API have their `__module__`
    # set to `"httpx"`.
    assert httpx.Client.__module__ == "httpx"
    assert httpx.HTTPStatusError.__module__ == "httpx"
    assert httpx.get.__module__ == "httpx"


def test_all_is_grouped_by_source_module() -> None:
    """
    The `httpx.__all__` listing is grouped by the module that each public
    name is imported from, with each group annotated by a comment.
    """
    source = pathlib.Path(httpx.__file__).read_text()

    # Comments are not part of the AST, so recover the groups from the source.
    pattern = re.compile(r"^\s*# (\S+)\n((?:\s+\"[^\"]+\",\n)+)", re.MULTILINE)
    groups = {
        comment: [name.strip('", ') for name in names.splitlines()]
        for comment, names in pattern.findall(source)
    }

    assert groups, "No `# <module>.py` groups found in `httpx/__init__.py`"
    assert sum(map(len, groups.values())) == len(httpx.__all__)

    # `__version__.py` and `_main.py` do not define `__all__`. The `_main`
    # module is also optional at runtime, so it must not be imported here.
    modules_without_exports = ("__version__.py", "_main.py")

    for module_comment, names in groups.items():
        if module_comment in modules_without_exports:
            for name in names:
                assert hasattr(httpx, name), f"{name!r} is not exported by httpx"
            continue
        module = importlib.import_module(f"httpx.{module_comment.removesuffix('.py')}")
        exported = getattr(module, "__all__", None)
        assert exported is not None, f"unexpected `__all__` in {module_comment}"
        assert names == exported, f"{module_comment} group does not match module export"


def _ast_all(source: str) -> list[str]:
    tree = ast.parse(source)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__all__"
    )
    value = assignment.value
    assert isinstance(value, ast.List)
    return [
        element.value
        for element in value.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]


def test_all_listing_is_valid() -> None:
    # The literal parsed from source must match the runtime `httpx.__all__`,
    # so that the grouping test above is checking the real listing.
    source = pathlib.Path(httpx.__file__).read_text()
    assert _ast_all(source) == list(httpx.__all__)
