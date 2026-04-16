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

    events_ch = MagicMock(spec=discord.TextChannel)
    events_ch.name = "events"
    alerts_ch = MagicMock(spec=discord.TextChannel)
    alerts_ch.name = "alerts"

    residents_ch = residents_channel
    if residents_ch is None:
        residents_ch_obj = None
    else:
        residents_ch_obj = residents_ch

    poller = EventPoller(
        gov, cache, events_ch, alerts_ch,
        residents_channel=residents_ch_obj,
    )

    routed: list[tuple[str, discord.Embed]] = []
    original_put = poller._message_queue.put

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
async def test_finding_routes_to_residents_not_events():
    residents_ch = _make_residents_channel()
    poller, routed = _make_poller(
        [{"event_id": 1, "type": "sentinel_finding", "severity": "high",
          "message": "m", "agent_id": "s", "agent_name": "S"}],
        residents_channel=residents_ch,
    )
    await poller._poll_loop_once()
    channels_hit = [name for name, _ in routed]
    assert "residents" in channels_hit
    assert "events" not in channels_hit


@pytest.mark.asyncio
async def test_lifecycle_event_routes_to_events_not_residents():
    residents_ch = _make_residents_channel()
    poller, routed = _make_poller(
        [{"event_id": 1, "type": "verdict_change", "severity": "warning",
          "message": "m", "agent_id": "a", "agent_name": "A",
          "from": "proceed", "to": "guide"}],
        residents_channel=residents_ch,
    )
    await poller._poll_loop_once()
    channels_hit = [name for name, _ in routed]
    assert "events" in channels_hit
    assert "residents" not in channels_hit


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
    assert "events" not in channels_hit


@pytest.mark.asyncio
async def test_finding_falls_back_to_events_without_residents_channel():
    poller, routed = _make_poller(
        [{"event_id": 1, "type": "sentinel_finding", "severity": "info",
          "message": "m", "agent_id": "s", "agent_name": "S"}],
        residents_channel=None,
    )
    await poller._poll_loop_once()
    channels_hit = [name for name, _ in routed]
    assert "events" in channels_hit
    assert "residents" not in channels_hit
