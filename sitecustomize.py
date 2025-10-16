"""Compatibility helpers for third-party tooling."""

from __future__ import annotations

import types

try:  # pragma: no cover - prefer backport when available
    import importlib_metadata as metadata  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - fallback for Python 3.12+
    from importlib import metadata  # type: ignore

_original_entry_points = metadata.entry_points


def _attach_get(entry_points_obj):
    """Ensure the returned entry points expose a ``get`` helper."""

    if hasattr(entry_points_obj, "get"):
        return entry_points_obj

    def _get(self, name, default=()):
        if hasattr(self, "select"):
            selected = self.select(group=name)
        else:
            selected = [
                entry_point
                for entry_point in self
                if entry_point.group == name
            ]
        if not selected:
            return default
        return selected

    entry_points_obj.get = types.MethodType(_get, entry_points_obj)
    return entry_points_obj


def _patched_entry_points(*args, **kwargs):
    entry_points_obj = _original_entry_points(*args, **kwargs)
    return _attach_get(entry_points_obj)


metadata.entry_points = _patched_entry_points
