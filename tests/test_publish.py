"""
Tests ensuring that pre-release versions of `httpx` can never be published
to the `httpx` package name on PyPI.

Pre-release versions such as `1.0.dev1` are sorted above the latest stable
release by tooling that allows pre-releases, so publishing one silently
replaces the documented API with an incompatible preview.
See https://github.com/encode/httpx/issues/3635
"""

import os
import pathlib
import re
import shutil
import subprocess

REPO_ROOT = pathlib.Path(__file__).parent.parent

PRE_RELEASE_VERSIONS = ["1.0.dev1", "1.0.dev2", "1.0a1", "1.0b2", "1.0rc1"]
STABLE_VERSIONS = ["0.28.1", "0.29.0", "1.0.0"]


def run_publish(tmp_path: pathlib.Path, version: str) -> int:
    """
    Run `scripts/publish` against a checkout whose `__version__` is `version`,
    with stubbed `twine` and `mkdocs` commands, returning the exit code.
    """
    checkout = tmp_path / "checkout"
    (checkout / "httpx").mkdir(parents=True)
    (checkout / "httpx" / "__version__.py").write_text(
        f'__title__ = "httpx"\n__version__ = "{version}"\n'
    )
    shutil.copy(REPO_ROOT / "scripts" / "publish", checkout / "publish")

    # Stub out the commands that would otherwise upload to PyPI or
    # deploy documentation, so the script can run to completion offline.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ("twine", "mkdocs"):
        stub = bin_dir / command
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env.pop("GITHUB_ACTIONS", None)

    return subprocess.run(
        ["sh", "publish"], cwd=checkout, env=env, capture_output=True
    ).returncode


def test_current_version_is_a_stable_release() -> None:
    # The version on this branch must never be a pre-release, since the
    # tag-triggered publish workflow publishes whatever version is here.
    content = (REPO_ROOT / "httpx" / "__version__.py").read_text()
    match = re.search(r'__version__ = "(.*?)"', content)
    assert match is not None
    assert re.fullmatch(r"\d+\.\d+\.\d+", match.group(1))


def test_publish_script_rejects_pre_release_versions(
    tmp_path: pathlib.Path,
) -> None:
    # `scripts/publish` must refuse to publish pre-release versions...
    for index, version in enumerate(PRE_RELEASE_VERSIONS):
        assert run_publish(tmp_path / str(index), version) != 0, version


def test_publish_script_allows_stable_versions(tmp_path: pathlib.Path) -> None:
    # ...while stable versions continue through to the upload step.
    for index, version in enumerate(STABLE_VERSIONS):
        assert run_publish(tmp_path / str(index), version) == 0, version
