"""Tests for bridge.extensions — the deferred extension loader."""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from bridge.extensions import Extension, ExtensionContext, load_extensions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx() -> ExtensionContext:
    """Return a minimal ExtensionContext with all fields mocked."""
    return ExtensionContext(
        guild=MagicMock(),
        channels={},
        gov_client=MagicMock(),
        anima_client=MagicMock(),
        cache=MagicMock(),
        bot=MagicMock(),
    )


def _make_extension_module(name: str, setup_fn=None, broken_import: bool = False):
    """Inject a synthetic bridge.deferred.<name> module into sys.modules.

    Returns the injected module so the test can inspect it.
    """
    if broken_import:
        # Simulate a module that raises ImportError
        if f"bridge.deferred.{name}" in sys.modules:
            del sys.modules[f"bridge.deferred.{name}"]
        return None

    mod = types.ModuleType(f"bridge.deferred.{name}")
    if setup_fn is not None:
        mod.setup = setup_fn
    sys.modules[f"bridge.deferred.{name}"] = mod
    return mod


# ---------------------------------------------------------------------------
# load_extensions
# ---------------------------------------------------------------------------

async def test_load_extensions_calls_setup_and_start():
    """A module with setup() returns a started extension."""
    ext = MagicMock(spec=Extension)
    ext.start = AsyncMock()
    ext.stop = AsyncMock()

    async def fake_setup(ctx):
        return ext

    _make_extension_module("test_good", setup_fn=fake_setup)
    ctx = _make_ctx()

    result = await load_extensions(["test_good"], ctx)

    assert len(result) == 1
    assert result[0] is ext
    ext.start.assert_called_once()


async def test_load_extensions_skips_missing_module():
    """A module that cannot be imported is skipped, not raised."""
    # Use a name that genuinely doesn't exist and is not in sys.modules
    sys.modules.pop("bridge.deferred.does_not_exist_xyz", None)
    ctx = _make_ctx()

    result = await load_extensions(["does_not_exist_xyz"], ctx)

    assert result == []


async def test_load_extensions_skips_no_setup():
    """A module without setup() is skipped with an error log."""
    _make_extension_module("test_no_setup", setup_fn=None)
    ctx = _make_ctx()

    result = await load_extensions(["test_no_setup"], ctx)

    assert result == []


async def test_load_extensions_skips_bad_setup():
    """A setup() that raises an exception is skipped."""
    async def bad_setup(ctx):
        raise RuntimeError("boom")

    _make_extension_module("test_bad_setup", setup_fn=bad_setup)
    ctx = _make_ctx()

    result = await load_extensions(["test_bad_setup"], ctx)

    assert result == []


async def test_load_extensions_multiple():
    """Multiple extensions are all loaded when they all succeed."""
    for i in range(3):
        ext = MagicMock(spec=Extension)
        ext.start = AsyncMock()
        ext.stop = AsyncMock()

        # Use a default-arg capture to avoid the loop-variable closure pitfall
        async def _setup(ctx, e=ext):
            return e

        _make_extension_module(f"test_multi_{i}", setup_fn=_setup)

    ctx = _make_ctx()
    loaded = await load_extensions(["test_multi_0", "test_multi_1", "test_multi_2"], ctx)

    assert len(loaded) == 3
