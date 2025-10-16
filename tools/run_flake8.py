"""Run flake8 with compatibility fixes for importlib.metadata."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, DefaultDict, List

try:  # pragma: no cover - prefer importlib_metadata when available
    import importlib_metadata as metadata  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - fallback for Python 3.12+
    from importlib import metadata  # type: ignore


_ORIGINAL_ENTRY_POINTS = metadata.entry_points


def _patched_entry_points(*args: Any, **kwargs: Any):
    entry_points_obj = _ORIGINAL_ENTRY_POINTS(*args, **kwargs)
    if hasattr(entry_points_obj, "get"):
        return entry_points_obj

    grouped: DefaultDict[str, List[Any]] = defaultdict(list)
    for entry_point in entry_points_obj:  # type: ignore[union-attr]
        grouped[entry_point.group].append(entry_point)
    return grouped


def main() -> None:
    metadata.entry_points = _patched_entry_points  # type: ignore[assignment]
    from flake8.main import cli

    cli.main()


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
