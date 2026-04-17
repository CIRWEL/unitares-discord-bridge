"""Tests for EventPoller finding/lifecycle routing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bridge.event_poller import EventPoller


def _make_poller(
    events: list[dict],
    *,
    residents_channel: discord.TextChannel | None = None,
) -> tuple[EventPoller, list[tuple[str, discord.Embed]]]:
    """Build an EventPoller wired to fake gov client + capture queue."""
    gov = MagicMock()
    gov.fetch_events = AsyncMock(return_value=events)
    gov.consecutive_failures = 0

    cache = MagicMock()
    cache.get_event_cursor = AsyncMock(return_value=0)
    cache.set_event_cursor = AsyncMock()

    activity_ch = MagicMock(spec=discord.TextChannel)
    activity_ch.name = "activity"
    signals_ch = MagicMock(spec=discord.TextChannel)
    signals_ch.name = "signals"
    alerts_ch = MagicMock(spec=discord.TextChannel)
    alerts_ch.name = "alerts"

    poller = EventPoller(
        gov, cache, activity_ch, signals_ch, alerts_ch,
        residents_channel=residents_channel,
    )

    routed: list[tuple[str, discord.Embed]] = []

    async def capture_put(item):
        channel, embed = item
        routed.append((channel.name, embed))

    poller._message_queue.put = capture_put
    return poller, routed


def _make_residents_channel() -> MagicMock:
    ch = MagicMock(spec=discord.TextChannel)
    ch.name = "residents"
    return ch


@pytest.mark.asyncio
async def test_finding_routes_to_residents_not_main_feed():
    residents_ch = _make_residents_channel()
    poller, routed = _make_poller(
        [{"event_id": 1, "type": "sentinel_finding", "severity": "high",
          "message": "m", "agent_id": "s", "agent_name": "S"}],
        residents_channel=residents_ch,
    )
    await poller._poll_loop_once()
    channels_hit = [name for name, _ in routed]
    assert "residents" in channels_hit
    assert "activity" not in channels_hit
    assert "signals" not in channels_hit


@pytest.mark.asyncio
async def test_verdict_change_routes_to_signals():
    residents_ch = _make_residents_channel()
    poller, routed = _make_poller(
        [{"event_id": 1, "type": "verdict_change", "severity": "warning",
          "message": "m", "agent_id": "a", "agent_name": "A",
          "from": "proceed", "to": "guide"}],
        residents_channel=residents_ch,
    )
    await poller._poll_loop_once()
    channels_hit = [name for name, _ in routed]
    assert "signals" in channels_hit
    assert "activity" not in channels_hit
    assert "residents" not in channels_hit


@pytest.mark.asyncio
async def test_agent_new_routes_to_activity():
    poller, routed = _make_poller(
        [{"event_id": 1, "type": "agent_new", "severity": "info",
          "message": "m", "agent_id": "n", "agent_name": "N"}],
    )
    await poller._poll_loop_once()
    channels_hit = [name for name, _ in routed]
    assert "activity" in channels_hit
    assert "signals" not in channels_hit


@pytest.mark.asyncio
async def test_agent_idle_routes_to_activity():
    poller, routed = _make_poller(
        [{"event_id": 1, "type": "agent_idle", "severity": "info",
          "message": "m", "agent_id": "i", "agent_name": "I"}],
    )
    await poller._poll_loop_once()
    channels_hit = [name for name, _ in routed]
    assert "activity" in channels_hit
    assert "signals" not in channels_hit


@pytest.mark.asyncio
async def test_drift_alert_routes_to_signals():
    poller, routed = _make_poller(
        [{"event_id": 1, "type": "drift_alert", "severity": "warning",
          "message": "m", "agent_id": "d", "agent_name": "D"}],
    )
    await poller._poll_loop_once()
    channels_hit = [name for name, _ in routed]
    assert "signals" in channels_hit
    assert "activity" not in channels_hit


@pytest.mark.asyncio
async def test_critical_finding_routes_to_residents_and_alerts():
    residents_ch = _make_residents_channel()
    poller, routed = _make_poller(
        [{"event_id": 1, "type": "watcher_finding", "severity": "critical",
          "message": "m", "agent_id": "w", "agent_name": "W"}],
        residents_channel=residents_ch,
    )
    await poller._poll_loop_once()
    channels_hit = [name for name, _ in routed]
    assert "residents" in channels_hit
    assert "alerts" in channels_hit
    assert "activity" not in channels_hit
    assert "signals" not in channels_hit


@pytest.mark.asyncio
async def test_finding_falls_back_to_signals_without_residents_channel():
    poller, routed = _make_poller(
        [{"event_id": 1, "type": "sentinel_finding", "severity": "info",
          "message": "m", "agent_id": "s", "agent_name": "S"}],
        residents_channel=None,
    )
    await poller._poll_loop_once()
    channels_hit = [name for name, _ in routed]
    assert "signals" in channels_hit
    assert "activity" not in channels_hit
    assert "residents" not in channels_hit


@pytest.mark.asyncio
async def test_int_event_id_advances_cursor():
    poller, _ = _make_poller(
        [{"event_id": 5, "type": "agent_new", "severity": "info",
          "message": "m", "agent_id": "a", "agent_name": "A"},
         {"event_id": 7, "type": "agent_idle", "severity": "info",
          "message": "m", "agent_id": "b", "agent_name": "B"}],
    )
    await poller._poll_loop_once()
    poller.cache.set_event_cursor.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_non_int_event_id_is_skipped_entirely():
    # REST /api/events supplements from the audit DB, which uses UUID
    # event_ids. Those events are incompatible with the cursor protocol
    # and get re-fetched every poll — so we drop them at ingest rather
    # than spam Discord and stall the cursor.
    poller, routed = _make_poller(
        [{"event_id": "fcd718be-0243-4a26-b503-79d4a3d7bfb1",
          "type": "cross_device_call", "severity": "info",
          "message": "m", "agent_id": "a", "agent_name": "A"}],
    )
    await poller._poll_loop_once()
    assert routed == []
    poller.cache.set_event_cursor.assert_not_awaited()


@pytest.mark.asyncio
async def test_mixed_event_ids_renders_int_only_and_advances_cursor():
    poller, routed = _make_poller(
        [{"event_id": "uuid-thing", "type": "cross_device_call", "severity": "info",
          "message": "m", "agent_id": "a", "agent_name": "A"},
         {"event_id": 3, "type": "agent_idle", "severity": "info",
          "message": "m", "agent_id": "b", "agent_name": "B"}],
    )
    await poller._poll_loop_once()
    # Only the int-id event renders
    channels_hit = [name for name, _ in routed]
    assert channels_hit == ["activity"]
    poller.cache.set_event_cursor.assert_awaited_once_with(3)
