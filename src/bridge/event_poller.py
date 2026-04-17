"""Poll governance-mcp for events and dispatch Discord embeds."""

from __future__ import annotations

import asyncio
import logging

import discord

from bridge.cache import BridgeCache
from bridge.embeds import classify_rest_event, event_to_embed, is_critical_event
from bridge.mcp_client import GovernanceClient
from bridge.tasks import cancel_tasks, create_logged_task

log = logging.getLogger(__name__)


class EventPoller:
    """Periodically fetches governance events and queues Discord messages."""

    def __init__(
        self,
        gov_client: GovernanceClient,
        cache: BridgeCache,
        activity_channel: discord.TextChannel,
        signals_channel: discord.TextChannel,
        alerts_channel: discord.TextChannel,
        interval: int = 10,
        audit_channel: discord.TextChannel | None = None,
        residents_channel: discord.TextChannel | None = None,
    ) -> None:
        self.gov = gov_client
        self.cache = cache
        self.activity_channel = activity_channel
        self.signals_channel = signals_channel
        self.alerts_channel = alerts_channel
        self.interval = interval
        self.audit_channel = audit_channel
        self.residents_channel = residents_channel
        self._task: asyncio.Task | None = None
        self._send_task: asyncio.Task | None = None
        self._gov_alert_sent: bool = False
        self._message_queue: asyncio.Queue[tuple[discord.TextChannel, discord.Embed]] = (
            asyncio.Queue(maxsize=100)
        )

    async def start(self) -> None:
        """Spawn the poll and send loops as background tasks."""
        self._task = create_logged_task(self._poll_loop(), name="event-poll")
        self._send_task = create_logged_task(self._send_loop(), name="event-send")

    async def stop(self) -> None:
        """Cancel both background tasks."""
        await cancel_tasks(self._task, self._send_task)

    async def _poll_loop(self) -> None:
        while True:
            await self._poll_loop_once()
            await asyncio.sleep(self.interval)

    async def _poll_loop_once(self) -> None:
        try:
            cursor = await self.cache.get_event_cursor()
            events = await self.gov.fetch_events(since=cursor)
            for event in events:
                embed = event_to_embed(event)
                is_finding = event.get("type", "").endswith("_finding")
                if is_finding and self.residents_channel is not None:
                    await self._message_queue.put((self.residents_channel, embed))
                else:
                    bucket = classify_rest_event(event)
                    target = (
                        self.activity_channel if bucket == "activity"
                        else self.signals_channel
                    )
                    await self._message_queue.put((target, embed))
                if is_critical_event(event):
                    await self._message_queue.put((self.alerts_channel, embed))
            if events:
                # Only advance the cursor on int event_ids. A past governance
                # schema drift emitted UUIDs here, and max() over mixed types
                # would poison the cursor — better to replay a few events than
                # crash every poll cycle.
                int_ids = [
                    e.get("event_id") for e in events
                    if isinstance(e.get("event_id"), int)
                ]
                if int_ids:
                    await self.cache.set_event_cursor(max(int_ids))
                else:
                    log.warning(
                        "No int event_ids in batch of %d; cursor not advanced",
                        len(events),
                    )
            if self.gov.consecutive_failures >= 3 and not self._gov_alert_sent:
                self._gov_alert_sent = True
                warn = discord.Embed(
                    title="Governance MCP Unreachable",
                    colour=discord.Colour.dark_red(),
                )
                await self._message_queue.put((self.alerts_channel, warn))
            elif self.gov.consecutive_failures == 0 and self._gov_alert_sent:
                self._gov_alert_sent = False
                recovered = discord.Embed(
                    title="Governance MCP Recovered",
                    colour=discord.Colour.green(),
                )
                await self._message_queue.put((self.alerts_channel, recovered))
        except Exception as exc:
            log.error("Event poll error: %s", exc)

    async def _send_loop(self) -> None:
        while True:
            channel, embed = await self._message_queue.get()
            try:
                await channel.send(embed=embed)
            except discord.RateLimited as exc:
                # Raised when max_ratelimit_timeout is set and the retry-after exceeds it.
                # Respect Discord's back-off and re-queue rather than dropping the message.
                log.warning("Global rate limit hit; retrying in %.1fs", exc.retry_after)
                await asyncio.sleep(exc.retry_after)
                await self._message_queue.put((channel, embed))
            except discord.HTTPException as exc:
                if exc.status == 429:
                    # Per-route 429 that discord.py's internal limiter did not absorb.
                    # Parse Retry-After from the response headers, fall back to 5 s.
                    retry_after = float(exc.response.headers.get("Retry-After", 5))
                    log.warning("Rate limited (HTTP 429); retrying in %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    await self._message_queue.put((channel, embed))
                else:
                    log.warning("Discord send failed: %s", exc)
            # 150 ms pacing between sends to stay well under Discord's per-route burst
            # limit — this is not a retry delay; rate limit retries are handled above.
            await asyncio.sleep(0.15)
