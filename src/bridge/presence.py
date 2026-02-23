"""Agent presence manager — tracks agent lifecycle on Discord."""

from __future__ import annotations

import asyncio

import discord


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
        self._task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        """Cancel the background cleanup loop."""
        if self._task:
            self._task.cancel()

    async def handle_new_agent(self, event: dict) -> None:
        """Called by event poller when agent_new fires."""
        agent_id = event.get("agent_id", "")
        agent_name = event.get("agent_name", "unknown")

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
        """Periodic cleanup -- placeholder for idle channel archival."""
        while True:
            await asyncio.sleep(self.interval * 10)
