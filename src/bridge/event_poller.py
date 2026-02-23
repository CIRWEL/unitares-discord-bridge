"""Poll governance-mcp for events and dispatch Discord embeds."""

from __future__ import annotations

import asyncio
import logging

import discord

from bridge.cache import BridgeCache
from bridge.embeds import event_to_embed, is_critical_event
from bridge.mcp_client import GovernanceClient
from bridge.resonance import CIRS_EVENT_TYPES
from bridge.tasks import create_logged_task

log = logging.getLogger(__name__)


class EventPoller:
    """Periodically fetches governance events and queues Discord messages."""

    def __init__(
        self,
        gov_client: GovernanceClient,
        cache: BridgeCache,
        events_channel: discord.TextChannel,
        alerts_channel: discord.TextChannel,
        interval: int = 10,
        presence_manager=None,
        poll_manager=None,
        resonance_tracker=None,
        audit_channel: discord.TextChannel | None = None,
        guild: discord.Guild | None = None,
    ) -> None:
        self.gov = gov_client
        self.cache = cache
        self.events_channel = events_channel
        self.alerts_channel = alerts_channel
        self.interval = interval
        self.presence = presence_manager
        self.poll_manager = poll_manager
        self.resonance = resonance_tracker
        self.audit_channel = audit_channel
        self.guild = guild
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
        for task in (self._task, self._send_task):
            if task:
                task.cancel()

    async def _poll_loop(self) -> None:
        while True:
            try:
                cursor = await self.cache.get_event_cursor()
                events = await self.gov.fetch_events(since=cursor)
                for event in events:
                    embed = event_to_embed(event)
                    await self._message_queue.put((self.events_channel, embed))
                    if is_critical_event(event):
                        await self._message_queue.put((self.alerts_channel, embed))
                    # Trigger poll for pause/reject verdicts
                    if (
                        self.poll_manager
                        and self.audit_channel
                        and self.guild
                        and event.get("type") == "verdict_change"
                        and event.get("to") in ("pause", "reject")
                    ):
                        try:
                            await self.poll_manager.handle_verdict_event(
                                event, self.alerts_channel, self.audit_channel, self.guild,
                            )
                        except Exception as e:
                            log.warning("Poll manager error: %s", e)
                    if self.presence and event.get("type") == "agent_new":
                        try:
                            await self.presence.handle_new_agent(event)
                        except Exception as e:
                            log.warning("Presence handler error: %s", e)
                    if self.resonance and event.get("type") in CIRS_EVENT_TYPES:
                        try:
                            await self.resonance.handle_event(event)
                        except Exception as e:
                            log.warning("Resonance handler error: %s", e)
                if events:
                    last_id = max(e.get("event_id", 0) for e in events)
                    await self.cache.set_event_cursor(last_id)
                if self.gov.consecutive_failures >= 3 and not self._gov_alert_sent:
                    self._gov_alert_sent = True
                    warn = discord.Embed(
                        title="Governance MCP Unreachable",
                        colour=discord.Colour.dark_red(),
                    )
                    await self._message_queue.put((self.alerts_channel, warn))
                elif self.gov.consecutive_failures == 0:
                    self._gov_alert_sent = False
            except Exception as exc:
                log.error("Event poll error: %s", exc)
            await asyncio.sleep(self.interval)

    async def _send_loop(self) -> None:
        while True:
            channel, embed = await self._message_queue.get()
            try:
                await channel.send(embed=embed)
            except discord.HTTPException as exc:
                log.warning("Discord send failed: %s", exc)
            await asyncio.sleep(0.15)
