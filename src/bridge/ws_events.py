"""WebSocket subscriber for governance broadcaster events.

Complements :mod:`bridge.event_poller` — which polls ``/api/events`` and
surfaces *synthesized* high-level events (verdict_change, drift_alert,
etc.) — by listening directly to the broadcaster firehose at
``/ws/eisv``. The broadcaster emits typed governance events that the
REST path does not see:

- ``lifecycle_*`` (paused, resumed, archived, created, loop_detected,
  stuck_detected, silent_critical)
- ``identity_*`` (drift, assurance_change)
- ``knowledge_*`` (write, confidence_clamped)
- ``circuit_breaker_*`` (trip, reset)

Every one of these was invisible in Discord before this module existed.

``eisv_update`` messages are intentionally dropped here because the
existing event_poller + HUD already cover the per-check-in path; this
subscriber only handles the classes of event that had no surface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Iterable, Optional

import discord
import websockets
import websockets.exceptions

from bridge.tasks import cancel_tasks, create_logged_task

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Process-local broadcaster-event ring buffer
# ---------------------------------------------------------------------------
# The bridge's WS subscriber receives every typed broadcaster event. Instead
# of asking governance to replay them later, we retain the last ~1000 events
# in-memory so slash commands like /digest can aggregate over a recent window.
# Bounded size — oldest entries drop out automatically.

_EVENT_RING_MAX = 1000
_event_ring: "deque[tuple[float, dict]]" = deque(maxlen=_EVENT_RING_MAX)


def record_event(event: dict) -> None:
    """Append an event to the ring buffer with a wall-clock receive timestamp."""
    _event_ring.append((time.time(), event))


def recent_events(within_seconds: float) -> list[dict]:
    """Return events received in the last ``within_seconds``, oldest first."""
    cutoff = time.time() - within_seconds
    return [evt for ts, evt in _event_ring if ts >= cutoff]


def event_ring_size() -> int:
    """Expose buffer size for tests + /digest reporting."""
    return len(_event_ring)


def _reset_event_ring_for_tests() -> None:
    """Clear the ring buffer — tests only."""
    _event_ring.clear()


# ---------------------------------------------------------------------------
# Pure helpers (testable without a network)
# ---------------------------------------------------------------------------


def ws_url_from_http(http_url: str) -> str:
    """Convert ``http(s)://host[:port]`` to ``ws(s)://host[:port]/ws/eisv``."""
    u = http_url.rstrip("/")
    if u.startswith("http://"):
        return "ws://" + u[len("http://"):] + "/ws/eisv"
    if u.startswith("https://"):
        return "wss://" + u[len("https://"):] + "/ws/eisv"
    # Assume already a ws(s) URL or host:port — best-effort append.
    return u + "/ws/eisv"


def _colour_for_tags(tags: list[str]) -> discord.Colour:
    lower = {str(t).lower() for t in tags}
    if "critical" in lower:
        return discord.Colour.red()
    if "high" in lower:
        return discord.Colour.orange()
    return discord.Colour.blue()


def broadcaster_event_to_embed(event: dict) -> Optional[discord.Embed]:
    """Map a broadcaster event dict to a Discord embed.

    Returns ``None`` if the event should be skipped entirely (most
    importantly ``eisv_update``, which is already handled elsewhere).
    Unknown event types fall through to a generic renderer so new event
    classes remain visible rather than being silently dropped.
    """
    t = event.get("type") or ""
    if not t or t == "eisv_update":
        return None

    agent_id = event.get("agent_id") or ""
    agent = (
        event.get("agent_label")
        or event.get("agent_name")
        or (str(agent_id)[:12] if agent_id else "system")
    )
    ts = event.get("timestamp")

    title = t.replace("_", " ")
    description = ""
    colour = discord.Colour.blue()

    if t.startswith("lifecycle_"):
        phase = t[len("lifecycle_"):]
        title = f"Lifecycle: {phase.replace('_', ' ')}"
        description = event.get("reason") or ""
        if phase in ("paused", "stuck_detected", "silent_critical"):
            colour = discord.Colour.red()
        elif phase == "loop_detected":
            colour = discord.Colour.orange()
        elif phase == "resumed":
            colour = discord.Colour.green()
        else:
            colour = discord.Colour.blurple()
    elif t.startswith("identity_"):
        sub = t[len("identity_"):]
        title = f"Identity: {sub.replace('_', ' ')}"
        description = event.get("detail") or ""
        if t == "identity_drift":
            colour = discord.Colour.orange()
    elif t.startswith("knowledge_"):
        if t == "knowledge_write":
            dtype = event.get("discovery_type") or "discovery"
            summary = event.get("summary") or ""
            if len(summary) > 200:
                summary = summary[:197] + "..."
            title = f"Knowledge write: {dtype}"
            description = summary
            tags = event.get("tags") or []
            if tags:
                colour = _colour_for_tags(tags)
        elif t == "knowledge_confidence_clamped":
            title = "Knowledge: confidence clamped"
            description = event.get("summary") or ""
            colour = discord.Colour.orange()
    elif t.startswith("circuit_breaker_"):
        action = "tripped" if t == "circuit_breaker_trip" else "reset"
        title = f"Circuit breaker {action}"
        description = event.get("reason") or ""
        colour = discord.Colour.red() if action == "tripped" else discord.Colour.green()

    # Discord embed description cap is 4096 chars; be defensive.
    if description and len(description) > 1000:
        description = description[:997] + "..."

    embed = discord.Embed(title=title, description=description, colour=colour)
    embed.add_field(name="Agent", value=str(agent), inline=True)
    embed.add_field(name="Type", value=t, inline=True)
    if ts:
        embed.set_footer(text=str(ts))
    return embed


def is_critical_broadcaster_event(event: dict) -> bool:
    """Also mirror to alerts channel if this fires."""
    t = event.get("type") or ""
    if t == "circuit_breaker_trip":
        return True
    if t in (
        "lifecycle_paused",
        "lifecycle_stuck_detected",
        "lifecycle_silent_critical",
    ):
        return True
    tags = event.get("tags") or []
    if any(str(tag).lower() == "critical" for tag in tags):
        return True
    return False


# ---------------------------------------------------------------------------
# Subscriber
# ---------------------------------------------------------------------------


def resolve_violation_class(event: dict, taxonomy_reverse: Optional[dict]) -> Optional[str]:
    """Map a broadcaster event to a violation class id using the taxonomy.

    Precedence:
    1. Explicit ``violation_class`` on the payload (Watcher emits this now).
    2. Reverse-lookup by event type in ``broadcast_events``.

    Returns the class id (e.g. ``"INT"``) or ``None`` if no mapping.
    """
    explicit = event.get("violation_class")
    if isinstance(explicit, str) and explicit:
        return explicit
    if not taxonomy_reverse:
        return None
    t = event.get("type") or ""
    return (taxonomy_reverse.get("broadcast_events") or {}).get(t)


class WSEventSubscriber:
    """Subscribe to ``/ws/eisv``, dispatch typed events to Discord.

    Runs alongside :class:`bridge.event_poller.EventPoller`. If either
    fails independently, the other keeps working.
    """

    def __init__(
        self,
        governance_url: str,
        events_channel: discord.TextChannel,
        alerts_channel: discord.TextChannel,
        reconnect_initial: float = 1.0,
        reconnect_max: float = 30.0,
        connect_kwargs: Optional[dict] = None,
        class_channels: Optional[dict[str, discord.TextChannel]] = None,
        taxonomy_reverse: Optional[dict] = None,
    ) -> None:
        self.ws_url = ws_url_from_http(governance_url)
        self.events_channel = events_channel
        self.alerts_channel = alerts_channel
        # Per-class channels: {"INT": channel, "ENT": channel, ...}. When a
        # matched event has a class in this map, it's ALSO posted to that
        # channel (in addition to the main #events feed). Disabled by passing
        # None or an empty dict.
        self.class_channels = class_channels or {}
        self.taxonomy_reverse = taxonomy_reverse or {}
        self.reconnect_initial = reconnect_initial
        self.reconnect_max = reconnect_max
        self._connect_kwargs = connect_kwargs or {
            "ping_interval": 20,
            "ping_timeout": 20,
        }
        self._sub_task: Optional[asyncio.Task] = None
        self._send_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._send_queue: asyncio.Queue[tuple[discord.TextChannel, discord.Embed]] = (
            asyncio.Queue(maxsize=100)
        )

    async def start(self) -> None:
        self._stop_event.clear()
        self._sub_task = create_logged_task(
            self._subscribe_loop(), name="ws-events-sub"
        )
        self._send_task = create_logged_task(
            self._send_loop(), name="ws-events-send"
        )

    async def stop(self) -> None:
        self._stop_event.set()
        await cancel_tasks(self._sub_task, self._send_task)

    async def _subscribe_loop(self) -> None:
        delay = self.reconnect_initial
        while not self._stop_event.is_set():
            try:
                log.info("WS events: connecting to %s", self.ws_url)
                async with websockets.connect(
                    self.ws_url, **self._connect_kwargs,
                ) as ws:
                    delay = self.reconnect_initial
                    log.info("WS events: connected")
                    async for raw in ws:
                        if self._stop_event.is_set():
                            break
                        try:
                            event = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if not isinstance(event, dict):
                            continue
                        await self._dispatch(event)
            except asyncio.CancelledError:
                raise
            except websockets.exceptions.ConnectionClosed as exc:
                log.info("WS events: connection closed (%s)", exc)
            except Exception as exc:
                log.warning("WS events: error (%s); retrying in %.1fs", exc, delay)
            # Backoff wait — interruptible by stop()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, self.reconnect_max)

    async def _dispatch(self, event: dict) -> None:
        # Record every typed event (including ones we don't turn into an
        # embed) so /digest can aggregate them later. This is the single
        # authoritative ingest point, so no other code needs to touch the
        # ring buffer.
        if event.get("type") and event.get("type") != "eisv_update":
            record_event(event)
        embed = broadcaster_event_to_embed(event)
        if embed is None:
            return
        try:
            self._send_queue.put_nowait((self.events_channel, embed))
        except asyncio.QueueFull:
            # Drop rather than block the websocket reader. The dashboard
            # is the authoritative event record anyway; Discord is a
            # human-facing surface with rate limits.
            log.warning("WS events: send queue full, dropping event %s",
                        event.get("type"))
            return
        if is_critical_broadcaster_event(event):
            try:
                self._send_queue.put_nowait((self.alerts_channel, embed))
            except asyncio.QueueFull:
                pass
        # Per-class mirror: when an event maps to a violation class and the
        # bridge has a channel for it, post there too. This is the core value
        # of class routing — operators can subscribe to a subset of classes
        # without seeing every event in #events.
        class_id = resolve_violation_class(event, self.taxonomy_reverse)
        if class_id:
            cls_channel = self.class_channels.get(class_id)
            if cls_channel is not None:
                try:
                    self._send_queue.put_nowait((cls_channel, embed))
                except asyncio.QueueFull:
                    pass

    async def _send_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                channel, embed = await self._send_queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await channel.send(embed=embed)
            except discord.RateLimited as exc:
                log.warning("WS events: rate limited, retry in %.1fs",
                            exc.retry_after)
                await asyncio.sleep(exc.retry_after)
                try:
                    self._send_queue.put_nowait((channel, embed))
                except asyncio.QueueFull:
                    pass
            except discord.HTTPException as exc:
                if exc.status == 429:
                    retry = float(exc.response.headers.get("Retry-After", 5))
                    await asyncio.sleep(retry)
                    try:
                        self._send_queue.put_nowait((channel, embed))
                    except asyncio.QueueFull:
                        pass
                else:
                    log.warning("WS events: discord send failed (%s)", exc)
            # 150 ms pacing between sends — matches event_poller to stay
            # well under Discord's per-route burst limits.
            await asyncio.sleep(0.15)
