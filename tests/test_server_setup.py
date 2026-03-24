"""Tests for bridge.server_setup — CHANNEL_STRUCTURE and ensure_server_structure."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bridge.server_setup import CHANNEL_STRUCTURE, ensure_server_structure


# ---------------------------------------------------------------------------
# CHANNEL_STRUCTURE
# ---------------------------------------------------------------------------

def test_channel_structure_contains_required_channels():
    """Spot-check that the expected channels are present in the structure."""
    all_channels = {
        ch for channels in CHANNEL_STRUCTURE.values() for ch in channels
    }
    assert "events" in all_channels
    assert "alerts" in all_channels
    assert "governance-hud" in all_channels
    assert "audit-log" in all_channels
    assert "lumen-sensors" in all_channels


def test_channel_structure_no_observer_or_lumen_roles():
    """Removed roles must not appear anywhere in the structure definition."""
    # After issue #12 fix, the ROLES dict was removed; confirm the module
    # no longer defines those role names at module level.
    import bridge.server_setup as ss
    assert not hasattr(ss, "ROLES"), (
        "ROLES should have been removed — 'observer' and 'lumen' roles were unused"
    )


# ---------------------------------------------------------------------------
# ensure_server_structure
# ---------------------------------------------------------------------------

def _make_guild(existing_channels=None, existing_categories=None):
    """Build a minimal mock discord.Guild."""
    guild = MagicMock(spec=discord.Guild)
    guild.categories = existing_categories or []
    guild.roles = []

    async def create_category(name, **kw):
        cat = MagicMock(spec=discord.CategoryChannel)
        cat.name = name
        cat.channels = []
        return cat

    async def create_text_channel(name, **kw):
        ch = MagicMock(spec=discord.TextChannel)
        ch.name = name
        return ch

    async def create_forum(name, **kw):
        ch = MagicMock(spec=discord.ForumChannel)
        ch.name = name
        return ch

    guild.create_category = AsyncMock(side_effect=create_category)
    guild.create_text_channel = AsyncMock(side_effect=create_text_channel)
    guild.create_forum = AsyncMock(side_effect=create_forum)
    return guild


async def test_ensure_server_structure_creates_all_channels():
    """When no channels exist, ensure_server_structure creates them all."""
    guild = _make_guild()
    channel_map = await ensure_server_structure(guild)

    expected = {ch for channels in CHANNEL_STRUCTURE.values() for ch in channels}
    assert set(channel_map.keys()) == expected


async def test_ensure_server_structure_returns_existing_channels():
    """Channels that already exist are reused, not recreated."""
    # Pre-populate one category with its channels
    gov_channels = []
    for ch_name in CHANNEL_STRUCTURE["GOVERNANCE"]:
        ch = MagicMock(spec=discord.TextChannel)
        ch.name = ch_name
        gov_channels.append(ch)

    cat = MagicMock(spec=discord.CategoryChannel)
    cat.name = "GOVERNANCE"
    cat.channels = gov_channels

    guild = _make_guild(existing_categories=[cat])
    channel_map = await ensure_server_structure(guild)

    # GOVERNANCE channels should be in the map
    for ch_name in CHANNEL_STRUCTURE["GOVERNANCE"]:
        assert ch_name in channel_map

    # create_text_channel should NOT have been called for GOVERNANCE channels
    for call in guild.create_text_channel.call_args_list:
        assert call.kwargs.get("category") is not cat
