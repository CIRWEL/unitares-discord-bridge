"""Knowledge sync — mirror knowledge graph entries as Discord forum posts."""

from __future__ import annotations

import asyncio
import logging

import discord

from bridge.cache import BridgeCache
from bridge.mcp_client import GovernanceClient
from bridge.tasks import create_logged_task
from bridge.utils import parse_tool_result as _parse_tool_result_util

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type colours for knowledge entries
# ---------------------------------------------------------------------------

TYPE_COLOURS = {
    "note": discord.Colour.blue(),
    "insight": discord.Colour.purple(),
    "bug_found": discord.Colour.red(),
    "improvement": discord.Colour.green(),
    "analysis": discord.Colour.teal(),
    "pattern": discord.Colour.gold(),
}

STATUS_EMOJI = {
    "open": "\U0001f7e2",       # green circle
    "resolved": "\u2705",       # check mark
    "archived": "\U0001f4e6",   # package
}


# ---------------------------------------------------------------------------
# Embed builder (pure function, easily testable)
# ---------------------------------------------------------------------------

def build_knowledge_embed(discovery: dict) -> discord.Embed:
    """Build a Discord embed for a knowledge graph entry.

    Parameters
    ----------
    discovery:
        Dict with keys like ``"title"``, ``"content"``, ``"type"``,
        ``"status"``, ``"agent_id"``, ``"created_at"``, ``"metadata"``.
    """
    entry_type = discovery.get("type", "note")
    colour = TYPE_COLOURS.get(entry_type, discord.Colour.greyple())
    status = discovery.get("status", "open")
    status_icon = STATUS_EMOJI.get(status, "?")

    embed = discord.Embed(
        title=discovery.get("title", "Untitled Discovery"),
        description=discovery.get("content", ""),
        colour=colour,
    )
    embed.add_field(name="Type", value=entry_type, inline=True)
    embed.add_field(name="Status", value=f"{status_icon} {status}", inline=True)

    agent_id = discovery.get("agent_id", "")
    if agent_id:
        embed.add_field(name="Agent", value=agent_id[:8], inline=True)

    created = discovery.get("created_at", "")
    if created:
        embed.set_footer(text=f"Created: {created}")

    metadata = discovery.get("metadata", {})
    if metadata and isinstance(metadata, dict):
        meta_lines = [f"**{k}**: {v}" for k, v in list(metadata.items())[:5]]
        embed.add_field(name="Metadata", value="\n".join(meta_lines), inline=False)

    return embed


# ---------------------------------------------------------------------------
# Knowledge Sync
# ---------------------------------------------------------------------------

class KnowledgeSync:
    """Poll the knowledge graph for recent entries and post them to a Discord forum."""

    def __init__(
        self,
        gov_client: GovernanceClient,
        cache: BridgeCache,
        forum_channel: discord.ForumChannel,
        interval: int = 60,
    ) -> None:
        self.gov = gov_client
        self.cache = cache
        self.forum = forum_channel
        self.interval = interval
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Spawn the poll loop."""
        self._task = create_logged_task(self._poll_loop(), name="knowledge-sync")

    async def stop(self) -> None:
        """Cancel the background task."""
        if self._task:
            self._task.cancel()

    # -- Main loop ----------------------------------------------------------

    async def _poll_loop(self) -> None:
        while True:
            try:
                result = await self.gov.call_tool(
                    "knowledge",
                    {"action": "search", "query": "*", "limit": 20},
                )
                if not result:
                    await asyncio.sleep(self.interval)
                    continue

                entries = self._parse_tool_result(result)
                if isinstance(entries, dict):
                    entries = entries.get("results", entries.get("entries", []))

                if not isinstance(entries, list):
                    await asyncio.sleep(self.interval)
                    continue

                for entry in entries:
                    discovery_id = (
                        entry.get("id")
                        or entry.get("discovery_id")
                        or entry.get("node_id", "")
                    )
                    if not discovery_id:
                        continue

                    # Already posted?
                    post_id = await self.cache.get_knowledge_post(discovery_id)
                    if post_id:
                        continue

                    await self._create_forum_post(discovery_id, entry)

            except Exception as exc:
                log.error("Knowledge sync error: %s", exc)
            await asyncio.sleep(self.interval)

    # -- Forum post creation ------------------------------------------------

    async def _create_forum_post(self, discovery_id: str, entry: dict) -> None:
        title = entry.get("title", "Untitled Discovery")
        # Truncate title to Discord's 100-char limit for thread names
        thread_name = title[:100] if len(title) > 100 else title

        embed = build_knowledge_embed(entry)
        thread_with_msg = await self.forum.create_thread(
            name=thread_name,
            embed=embed,
        )
        await self.cache.set_knowledge_post(
            discovery_id, thread_with_msg.thread.id,
        )
        log.info("Created knowledge post for %s: %s", discovery_id, thread_name)

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _parse_tool_result(result: dict | None) -> dict | list:
        """Delegate to the shared utility (issue #7)."""
        return _parse_tool_result_util(result)


# ---------------------------------------------------------------------------
# Extension entry point (issue #1 — extensions.py requires this)
# ---------------------------------------------------------------------------

async def setup(ctx) -> "KnowledgeSync":  # ctx: ExtensionContext
    """Create and return a KnowledgeSync, creating the forum channel if needed."""
    from bridge.extensions import ExtensionContext
    assert isinstance(ctx, ExtensionContext)

    forum = discord.utils.get(ctx.guild.forums, name="knowledge-graph")
    if forum is None:
        gov_cat = discord.utils.get(ctx.guild.categories, name="GOVERNANCE")
        forum = await ctx.guild.create_forum(
            name="knowledge-graph",
            category=gov_cat,
            topic="Knowledge graph discoveries from the UNITARES governance system",
        )
        log.info("Created knowledge-graph forum channel")

    return KnowledgeSync(
        gov_client=ctx.gov_client,
        cache=ctx.cache,
        forum_channel=forum,
    )
