from __future__ import annotations

from pathlib import Path


_BLOCKED_DIRS = {"out", "_tmp", ".pytest_cache", ".pytest_tmp"}
_BLOCKED_PREFIXES = ("pytest-cache-files-", ".tmp_pytest")


def _is_blocked(path: Path) -> bool:
    for part in path.parts:
        name = part.lower()
        if name in _BLOCKED_DIRS:
            return True
        if any(name.startswith(prefix) for prefix in _BLOCKED_PREFIXES):
            return True
    return False


def pytest_ignore_collect(collection_path, config) -> bool:  # noqa: ARG001
    return _is_blocked(Path(str(collection_path)))
