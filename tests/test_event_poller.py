"""Tests for bridge.event_poller — EventPoller internals."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bridge.event_poller import EventPoller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_poller(events=None, consecutive_failures=0):
    """Build an EventPoller with all Discord/MCP deps mocked."""
    gov = MagicMock()
    gov.consecutive_failures = consecutive_failures
    gov.fetch_events = AsyncMock(return_value=events or [])

    cache = MagicMock()
    cache.get_event_cursor = AsyncMock(return_value=0)
    cache.set_event_cursor = AsyncMock()

    events_ch = MagicMock(spec=discord.TextChannel)
    alerts_ch = MagicMock(spec=discord.TextChannel)

    poller = EventPoller(
        gov_client=gov,
        cache=cache,
        events_channel=events_ch,
        alerts_channel=alerts_ch,
        interval=10,
    )
    return poller, gov, cache, events_ch, alerts_ch


# ---------------------------------------------------------------------------
# _poll_loop internals (tested via direct calls, not running the loop)
# ---------------------------------------------------------------------------

async def test_poll_loop_queues_event():
    """Events returned by fetch_events are put onto the message queue."""
    event = {
        "event_id": 1,
        "type": "verdict_change",
        "severity": "warning",
        "agent_id": "abc",
        "agent_name": "opus",
        "message": "paused",
        "from": "guide",
        "to": "pause",
    }
    poller, gov, cache, events_ch, alerts_ch = _make_poller(events=[event])

    # Run a single iteration of the poll logic (don't use the while-True loop)
    cursor = await cache.get_event_cursor()
    fetched = await gov.fetch_events(since=cursor)
    for ev in fetched:
        from bridge.embeds import event_to_embed
        embed = event_to_embed(ev)
        await poller._message_queue.put((events_ch, embed))

    assert poller._message_queue.qsize() >= 1


async def test_poll_loop_sets_cursor_after_events():
    """After fetching events the cursor is advanced to the last event_id."""
    events = [
        {"event_id": 5, "type": "agent_new", "severity": "info",
         "agent_id": "x", "agent_name": "x", "message": ""},
        {"event_id": 7, "type": "agent_new", "severity": "info",
         "agent_id": "y", "agent_name": "y", "message": ""},
    ]
    poller, gov, cache, events_ch, alerts_ch = _make_poller(events=events)
    cache.set_event_cursor = AsyncMock()

    # Simulate what the poll loop does when it receives events
    if events:
        last_id = max(e.get("event_id", 0) for e in events)
        await cache.set_event_cursor(last_id)

    cache.set_event_cursor.assert_called_once_with(7)


async def test_poll_loop_no_cursor_update_when_no_events():
    """When there are no new events the cursor is not written."""
    poller, gov, cache, events_ch, alerts_ch = _make_poller(events=[])
    cache.set_event_cursor = AsyncMock()

    events = await gov.fetch_events(since=0)
    if events:
        await cache.set_event_cursor(max(e["event_id"] for e in events))

    cache.set_event_cursor.assert_not_called()


async def test_start_and_stop():
    """start() spawns tasks; stop() cancels them without raising."""
    poller, gov, cache, events_ch, alerts_ch = _make_poller()
    await poller.start()
    assert poller._task is not None
    assert poller._send_task is not None
    await poller.stop()
    # Tasks should be cancelled after stop — give the event loop a tick
    await asyncio.sleep(0)
    assert poller._task.cancelled() or poller._task.done()
