import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bridge.deferred.resonance import (
    ResonanceTracker,
    build_resonance_alert_embed,
    build_state_update_embed,
    build_coherence_embed,
    build_stability_embed,
    _resonance_key,
    CIRS_EVENT_TYPES,
)


# ---------------------------------------------------------------------------
# Embed builder tests (pure functions)
# ---------------------------------------------------------------------------

def test_resonance_alert_embed_gold():
    event = {
        "type": "RESONANCE_ALERT",
        "agent_a": "aaa-111",
        "agent_b": "bbb-222",
        "agent_a_name": "Opus",
        "agent_b_name": "Sonnet",
        "message": "Coupling detected",
        "severity": "warning",
        "coupling_strength": 0.87,
    }
    embed = build_resonance_alert_embed(event)
    assert isinstance(embed, discord.Embed)
    assert embed.colour == discord.Colour.gold()
    assert "Opus" in embed.title
    assert "Sonnet" in embed.title
    assert "\u2194" in embed.title


def test_resonance_alert_embed_shows_both_agents():
    event = {
        "agent_a_name": "Alice",
        "agent_b_name": "Bob",
        "severity": "info",
    }
    embed = build_resonance_alert_embed(event)
    field_values = [f.value for f in embed.fields]
    assert "Alice" in field_values
    assert "Bob" in field_values


def test_resonance_alert_embed_fallback_ids():
    event = {"agent_a": "aaa", "agent_b": "bbb"}
    embed = build_resonance_alert_embed(event)
    assert "aaa" in embed.title
    assert "bbb" in embed.title


def test_resonance_alert_embed_coupling_strength():
    event = {"agent_a": "a", "agent_b": "b", "coupling_strength": 0.5}
    embed = build_resonance_alert_embed(event)
    found = any("0.50" in str(f.value) for f in embed.fields)
    assert found


def test_resonance_alert_embed_no_coupling():
    event = {"agent_a": "a", "agent_b": "b"}
    embed = build_resonance_alert_embed(event)
    field_names = [f.name for f in embed.fields]
    assert "Coupling Strength" not in field_names


def test_state_update_embed_blue():
    event = {
        "type": "STATE_ANNOUNCE",
        "agent_id": "aaa-111",
        "agent_name": "Opus",
        "message": "State changed",
        "state": {"warmth": 0.7, "clarity": 0.6},
    }
    embed = build_state_update_embed(event)
    assert isinstance(embed, discord.Embed)
    assert embed.colour == discord.Colour.blue()
    assert "Opus" in embed.title


def test_state_update_embed_includes_state_fields():
    event = {
        "agent_name": "Opus",
        "state": {"warmth": 0.7, "clarity": 0.6},
    }
    embed = build_state_update_embed(event)
    field_names = [f.name for f in embed.fields]
    assert "Warmth" in field_names
    assert "Clarity" in field_names


def test_state_update_embed_no_state():
    event = {"agent_name": "Opus", "message": "No state data"}
    embed = build_state_update_embed(event)
    assert isinstance(embed, discord.Embed)


def test_coherence_embed_purple():
    event = {
        "type": "COHERENCE_REPORT",
        "agent_a": "aaa",
        "agent_b": "bbb",
        "agent_a_name": "Opus",
        "agent_b_name": "Sonnet",
        "message": "Coherence measured",
        "metrics": {"warmth_delta": 0.12, "clarity_delta": 0.05},
        "coherence": 0.85,
    }
    embed = build_coherence_embed(event)
    assert isinstance(embed, discord.Embed)
    assert embed.colour == discord.Colour.purple()
    assert "\u2194" in embed.title


def test_coherence_embed_shows_metrics():
    event = {
        "agent_a": "a",
        "agent_b": "b",
        "metrics": {"warmth_delta": 0.12, "clarity_delta": 0.05},
        "coherence": 0.85,
    }
    embed = build_coherence_embed(event)
    field_names = [f.name for f in embed.fields]
    assert "Warmth Delta" in field_names
    assert "Overall Coherence" in field_names


def test_coherence_embed_no_metrics():
    event = {"agent_a": "a", "agent_b": "b"}
    embed = build_coherence_embed(event)
    assert isinstance(embed, discord.Embed)


def test_stability_embed_green():
    event = {
        "type": "STABILITY_RESTORED",
        "agent_a": "aaa",
        "agent_b": "bbb",
        "agent_a_name": "Opus",
        "agent_b_name": "Sonnet",
        "message": "Agents stabilized",
        "duration": "5m 32s",
    }
    embed = build_stability_embed(event)
    assert isinstance(embed, discord.Embed)
    assert embed.colour == discord.Colour.green()
    assert "Opus" in embed.title
    assert "Sonnet" in embed.title


def test_stability_embed_shows_duration():
    event = {"agent_a": "a", "agent_b": "b", "duration": "3m 10s"}
    embed = build_stability_embed(event)
    found = any("3m 10s" in str(f.value) for f in embed.fields)
    assert found


def test_stability_embed_no_duration():
    event = {"agent_a": "a", "agent_b": "b"}
    embed = build_stability_embed(event)
    field_names = [f.name for f in embed.fields]
    assert "Duration" not in field_names


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

def test_resonance_key_sorted():
    assert _resonance_key("bbb", "aaa") == "aaa-bbb"
    assert _resonance_key("aaa", "bbb") == "aaa-bbb"


def test_cirs_event_types():
    assert "RESONANCE_ALERT" in CIRS_EVENT_TYPES
    assert "STATE_ANNOUNCE" in CIRS_EVENT_TYPES
    assert "COHERENCE_REPORT" in CIRS_EVENT_TYPES
    assert "STABILITY_RESTORED" in CIRS_EVENT_TYPES
    assert "agent_new" not in CIRS_EVENT_TYPES


# ---------------------------------------------------------------------------
# ResonanceTracker async tests
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_channel():
    ch = AsyncMock(spec=discord.TextChannel)
    return ch


@pytest.fixture
def tracker(mock_channel):
    return ResonanceTracker(mock_channel)


@pytest.mark.asyncio
async def test_tracker_creates_thread_on_resonance_alert(tracker, mock_channel):
    mock_msg = AsyncMock()
    mock_thread = AsyncMock(spec=discord.Thread)
    mock_msg.create_thread = AsyncMock(return_value=mock_thread)
    mock_channel.send = AsyncMock(return_value=mock_msg)

    event = {
        "type": "RESONANCE_ALERT",
        "agent_a": "aaa",
        "agent_b": "bbb",
        "agent_a_name": "Opus",
        "agent_b_name": "Sonnet",
        "message": "Coupling detected",
        "severity": "warning",
    }
    await tracker.handle_event(event)

    mock_channel.send.assert_called_once()
    mock_msg.create_thread.assert_called_once()
    thread_name = mock_msg.create_thread.call_args[1]["name"]
    assert "Opus" in thread_name
    assert "Sonnet" in thread_name
    assert "aaa-bbb" in tracker._active_threads


@pytest.mark.asyncio
async def test_tracker_posts_state_to_active_thread(tracker, mock_channel):
    # Set up an active thread
    mock_thread = AsyncMock(spec=discord.Thread)
    tracker._active_threads["aaa-bbb"] = mock_thread

    event = {
        "type": "STATE_ANNOUNCE",
        "agent_id": "aaa",
        "agent_name": "Opus",
        "message": "State changed",
    }
    await tracker.handle_event(event)

    mock_thread.send.assert_called_once()
    embed_arg = mock_thread.send.call_args[1]["embed"]
    assert embed_arg.colour == discord.Colour.blue()


@pytest.mark.asyncio
async def test_tracker_ignores_state_without_active_thread(tracker):
    event = {
        "type": "STATE_ANNOUNCE",
        "agent_id": "zzz",
        "agent_name": "Unknown",
    }
    await tracker.handle_event(event)
    # No error, no thread interaction


@pytest.mark.asyncio
async def test_tracker_posts_coherence_to_active_thread(tracker):
    mock_thread = AsyncMock(spec=discord.Thread)
    tracker._active_threads["aaa-bbb"] = mock_thread

    event = {
        "type": "COHERENCE_REPORT",
        "agent_a": "aaa",
        "agent_b": "bbb",
        "metrics": {"warmth_delta": 0.1},
    }
    await tracker.handle_event(event)

    mock_thread.send.assert_called_once()
    embed_arg = mock_thread.send.call_args[1]["embed"]
    assert embed_arg.colour == discord.Colour.purple()


@pytest.mark.asyncio
async def test_tracker_archives_thread_on_stability_restored(tracker):
    mock_thread = AsyncMock(spec=discord.Thread)
    mock_thread.name = "Resonance: Opus - Sonnet"
    tracker._active_threads["aaa-bbb"] = mock_thread

    event = {
        "type": "STABILITY_RESTORED",
        "agent_a": "aaa",
        "agent_b": "bbb",
        "message": "Stability restored",
    }
    await tracker.handle_event(event)

    mock_thread.send.assert_called_once()
    mock_thread.edit.assert_called_once_with(archived=True)
    assert "aaa-bbb" not in tracker._active_threads


@pytest.mark.asyncio
async def test_tracker_does_not_duplicate_thread(tracker, mock_channel):
    """Second RESONANCE_ALERT for same pair posts to existing thread."""
    mock_thread = AsyncMock(spec=discord.Thread)
    tracker._active_threads["aaa-bbb"] = mock_thread

    event = {
        "type": "RESONANCE_ALERT",
        "agent_a": "aaa",
        "agent_b": "bbb",
        "agent_a_name": "Opus",
        "agent_b_name": "Sonnet",
    }
    await tracker.handle_event(event)

    # Should post to existing thread, not create a new one
    mock_channel.send.assert_not_called()
    mock_thread.send.assert_called_once()


@pytest.mark.asyncio
async def test_tracker_handles_missing_agent_pair_gracefully(tracker, mock_channel):
    """RESONANCE_ALERT without agent pair should not crash."""
    event = {"type": "RESONANCE_ALERT", "message": "Incomplete event"}
    await tracker.handle_event(event)
    mock_channel.send.assert_not_called()
