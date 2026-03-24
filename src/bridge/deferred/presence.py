"""Agent presence manager — tracks agent lifecycle on Discord."""

from __future__ import annotations

import asyncio
import logging

import discord

from bridge.tasks import create_logged_task

log = logging.getLogger(__name__)


_VERDICT_ROLE_MAP: dict[str, str] = {
    "proceed": "agent-active",
    "guide": "agent-boundary",
    "pause": "agent-degraded",
    "reject": "agent-degraded",
}


def verdict_to_role_name(verdict: str) -> str:
    """Map a governance verdict string to a Discord role name."""
    return _VERDICT_ROLE_MAP.get(verdict, "agent-active")


class PresenceManager:
    """Manages Discord channels and embeds for agent presence events."""

    MAX_AGENT_CHANNELS = 20

    def __init__(
        self,
        gov_client,
        cache,
        guild: discord.Guild,
        agents_category: discord.CategoryChannel,
        lobby_channel: discord.TextChannel,
        interval: int = 30,
    ) -> None:
        self.gov = gov_client
        self.cache = cache
        self.guild = guild
        self.agents_category = agents_category
        self.lobby_channel = lobby_channel
        self.interval = interval
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background cleanup loop."""
        self._task = create_logged_task(self._cleanup_loop(), name="presence-cleanup")

    async def stop(self) -> None:
        """Cancel the background cleanup loop."""
        if self._task:
            self._task.cancel()

    async def handle_new_agent(self, event: dict) -> None:
        """Called by event poller when agent_new fires."""
        agent_id = event.get("agent_id", "")
        agent_name = event.get("agent_name", "unknown")
        log.info("New agent event: %s (%s)", agent_name, agent_id[:8])

        # Announce in lobby
        embed = discord.Embed(
            title="Agent Online",
            description=f"**{agent_name}** joined",
            colour=discord.Colour.green(),
        )
        await self.lobby_channel.send(embed=embed)

        # Create channel if under limit
        existing = await self.cache.get_agent_channel(agent_id)
        if not existing:
            active_count = len(self.agents_category.channels)
            if active_count < self.MAX_AGENT_CHANNELS:
                ch = await self.guild.create_text_channel(
                    name=f"agent-{agent_name[:30]}",
                    category=self.agents_category,
                    topic=f"Check-ins for {agent_name} ({agent_id[:8]}...)",
                )
                await self.cache.set_agent_channel(agent_id, ch.id)
                log.info("Created channel #agent-%s for %s", agent_name[:30], agent_id[:8])
            else:
                log.warning(
                    "Agent channel limit (%d) reached, skipping channel for %s",
                    self.MAX_AGENT_CHANNELS, agent_name,
                )

    async def post_checkin(self, agent_id: str, checkin_data: dict) -> None:
        """Post a check-in embed to the agent's channel."""
        channel_id = await self.cache.get_agent_channel(agent_id)
        if not channel_id:
            return
        ch = self.guild.get_channel(channel_id)
        if not ch:
            return

        verdict = checkin_data.get("verdict", "?")
        embed = discord.Embed(
            title="Check-in",
            description=checkin_data.get("response_text", ""),
            colour=discord.Colour.green()
            if verdict == "proceed"
            else discord.Colour.orange(),
        )
        eisv = checkin_data.get("eisv", {})
        if eisv:
            embed.add_field(
                name="EISV",
                value=(
                    f"E={eisv.get('E', 0):.2f} "
                    f"I={eisv.get('I', 0):.2f} "
                    f"S={eisv.get('S', 0):.2f} "
                    f"V={eisv.get('V', 0):.2f}"
                ),
                inline=False,
            )
        embed.add_field(name="Verdict", value=verdict, inline=True)
        await ch.send(embed=embed)

    async def _cleanup_loop(self) -> None:
        """Evict the least-recently-active agent channel when at capacity.

        Previously a no-op stub (issue #5).  Now: when the AGENTS category
        holds MAX_AGENT_CHANNELS or more channels, delete the one whose
        last_message_id is oldest (proxy for least-recently-active) and
        remove its entry from the cache so the slot can be reused.
        """
        while True:
            await asyncio.sleep(self.interval * 10)
            try:
                channels = [
                    ch for ch in self.agents_category.channels
                    if isinstance(ch, discord.TextChannel)
                ]
                if len(channels) < self.MAX_AGENT_CHANNELS:
                    continue  # Nothing to evict

                # Sort ascending by last_message_id; None sorts to front (oldest)
                oldest = min(channels, key=lambda ch: ch.last_message_id or 0)
                log.info(
                    "Agent channel limit reached (%d/%d), evicting #%s",
                    len(channels), self.MAX_AGENT_CHANNELS, oldest.name,
                )
                await oldest.delete(
                    reason="presence cleanup: evicting least-recently-active agent channel"
                )
                await self.cache.delete_agent_channel_by_channel_id(oldest.id)
            except Exception as exc:
                log.error("Presence cleanup error: %s", exc)


# ---------------------------------------------------------------------------
# Extension entry point (issue #1 — extensions.py requires this)
# ---------------------------------------------------------------------------

async def setup(ctx) -> "PresenceManager":  # ctx: ExtensionContext
    """Create and return a PresenceManager, creating the AGENTS category if needed."""
    from bridge.extensions import ExtensionContext
    assert isinstance(ctx, ExtensionContext)

    # Find or create the AGENTS category for per-agent channels
    category = discord.utils.get(ctx.guild.categories, name="AGENTS")
    if category is None:
        category = await ctx.guild.create_category("AGENTS")
        log.info("Created AGENTS category")

    # Use the events channel as the lobby for new-agent announcements
    lobby = ctx.channels.get("events")

    return PresenceManager(
        gov_client=ctx.gov_client,
        cache=ctx.cache,
        guild=ctx.guild,
        agents_category=category,
        lobby_channel=lobby,
    )
