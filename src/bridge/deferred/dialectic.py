"""Dialectic sync — mirror governance dialectic sessions as Discord forum posts."""

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
# Embed builders (pure functions, easily testable)
# ---------------------------------------------------------------------------

def build_dialectic_post_embed(session: dict) -> discord.Embed:
    """Build the opening embed for a new dialectic forum post."""
    embed = discord.Embed(
        title=f"Dialectic: {session.get('session_type', 'recovery')}",
        description=session.get("reason", "No reason given"),
        colour=discord.Colour.dark_gold(),
    )
    embed.add_field(name="Agent", value=session.get("paused_agent_id", "?")[:8], inline=True)
    embed.add_field(name="Reviewer", value=session.get("reviewer_agent_id", "?")[:8], inline=True)
    embed.add_field(name="Phase", value=session.get("phase", "?"), inline=True)
    embed.add_field(name="Session ID", value=session.get("session_id", "?"), inline=False)
    return embed


def build_thesis_embed(message: dict) -> discord.Embed:
    """Format a thesis submission."""
    embed = discord.Embed(title="Thesis", colour=discord.Colour.blue())
    embed.description = message.get("reasoning", "")
    if message.get("root_cause"):
        embed.add_field(name="Root Cause", value=message["root_cause"], inline=False)
    conditions = message.get("proposed_conditions", [])
    if conditions:
        embed.add_field(
            name="Proposed Conditions",
            value="\n".join(f"- {c}" for c in conditions),
            inline=False,
        )
    return embed


def build_antithesis_embed(message: dict) -> discord.Embed:
    """Format an antithesis submission."""
    embed = discord.Embed(title="Antithesis", colour=discord.Colour.orange())
    embed.description = message.get("reasoning", "")
    concerns = message.get("concerns", [])
    if concerns:
        embed.add_field(
            name="Concerns",
            value="\n".join(f"- {c}" for c in concerns),
            inline=False,
        )
    metrics = message.get("observed_metrics", {})
    if metrics:
        lines = [f"**{k}**: {v}" for k, v in metrics.items()]
        embed.add_field(name="Observed Metrics", value="\n".join(lines), inline=False)
    return embed


def build_synthesis_embed(message: dict, round_num: int) -> discord.Embed:
    """Format a synthesis submission."""
    agreed = message.get("agrees", False)
    colour = discord.Colour.green() if agreed else discord.Colour.greyple()
    embed = discord.Embed(title=f"Synthesis (Round {round_num})", colour=colour)
    embed.description = message.get("reasoning", "")
    if agreed:
        embed.add_field(name="Status", value="AGREES", inline=True)
    conditions = message.get("proposed_conditions", [])
    if conditions:
        embed.add_field(
            name="Proposed Conditions",
            value="\n".join(f"- {c}" for c in conditions),
            inline=False,
        )
    return embed


def build_resolution_embed(resolution: dict) -> discord.Embed:
    """Format the final resolution."""
    action = resolution.get("action", "unknown")
    colour = discord.Colour.green() if action == "resume" else discord.Colour.red()
    embed = discord.Embed(title=f"Resolution: {action.upper()}", colour=colour)
    embed.description = resolution.get("reasoning", "")
    embed.add_field(name="Root Cause", value=resolution.get("root_cause", "?"), inline=False)
    conditions = resolution.get("conditions", [])
    if conditions:
        embed.add_field(
            name="Conditions",
            value="\n".join(f"- {c}" for c in conditions),
            inline=False,
        )
    return embed


# ---------------------------------------------------------------------------
# Dialectic Sync
# ---------------------------------------------------------------------------

class DialecticSync:
    """Poll for active dialectic sessions and mirror them as Discord forum posts."""

    def __init__(
        self,
        gov_client: GovernanceClient,
        cache: BridgeCache,
        forum_channel: discord.ForumChannel,
        interval: int = 15,
    ) -> None:
        self.gov = gov_client
        self.cache = cache
        self.forum = forum_channel
        self.interval = interval
        self._known_sessions: dict[str, dict] = {}  # session_id -> last known state
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Spawn the poll loop."""
        self._task = create_logged_task(self._poll_loop(), name="dialectic-sync")

    async def stop(self) -> None:
        """Cancel the background task."""
        if self._task:
            self._task.cancel()

    # -- Main loop ----------------------------------------------------------

    async def _poll_loop(self) -> None:
        while True:
            try:
                # List active sessions via the dialectic tool
                result = await self.gov.call_tool("dialectic", {"action": "list"})
                if not result:
                    await asyncio.sleep(self.interval)
                    continue

                sessions = self._parse_tool_result(result)
                if not isinstance(sessions, list):
                    sessions = sessions.get("sessions", [])

                for session in sessions:
                    sid = session.get("session_id", "")
                    if not sid:
                        continue

                    # New session?  Create forum post
                    post_id = await self.cache.get_dialectic_post(sid)
                    if not post_id:
                        await self._create_forum_post(session)
                    else:
                        # Existing session -- check for new messages
                        await self._update_forum_post(sid, session, post_id)

            except Exception as exc:
                log.error("Dialectic sync error: %s", exc)
            await asyncio.sleep(self.interval)

    # -- Forum post lifecycle -----------------------------------------------

    async def _create_forum_post(self, session: dict) -> None:
        sid = session["session_id"]
        embed = build_dialectic_post_embed(session)

        # create_thread on a ForumChannel returns a ThreadWithMessage
        thread_with_msg = await self.forum.create_thread(
            name=f"Dialectic {sid[:8]} — {session.get('session_type', 'recovery')}",
            embed=embed,
        )
        await self.cache.set_dialectic_post(sid, thread_with_msg.thread.id)
        self._known_sessions[sid] = {"phase": session.get("phase"), "message_count": 0}

    async def _update_forum_post(
        self, sid: str, session: dict, thread_id: int,
    ) -> None:
        # Get detailed session info including messages
        detail = await self.gov.call_tool("dialectic", {"action": "get", "session_id": sid})
        if not detail:
            return

        data = self._parse_tool_result(detail)
        if isinstance(data, list):
            return  # unexpected shape
        messages = data.get("messages", [])
        known = self._known_sessions.get(sid, {"message_count": 0})

        # Post new messages
        if len(messages) > known.get("message_count", 0):
            thread = self.forum.guild.get_thread(thread_id)
            if not thread:
                return

            new_msgs = messages[known["message_count"]:]
            for msg in new_msgs:
                phase = msg.get("phase", "")
                if phase == "thesis":
                    embed = build_thesis_embed(msg)
                elif phase == "antithesis":
                    embed = build_antithesis_embed(msg)
                elif phase == "synthesis":
                    round_num = known.get("synthesis_round", 0) + 1
                    embed = build_synthesis_embed(msg, round_num)
                    known["synthesis_round"] = round_num
                else:
                    continue
                await thread.send(embed=embed)

            known["message_count"] = len(messages)

        # Check for resolution
        if session.get("phase") in ("resolved", "escalated", "failed"):
            resolution = data.get("resolution")
            if resolution and not known.get("resolved"):
                thread = self.forum.guild.get_thread(thread_id)
                if thread:
                    embed = build_resolution_embed(resolution)
                    await thread.send(embed=embed)
                    known["resolved"] = True

        self._known_sessions[sid] = known

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _parse_tool_result(result: dict | None) -> dict | list:
        """Delegate to the shared utility (issue #7)."""
        return _parse_tool_result_util(result)


# ---------------------------------------------------------------------------
# Extension entry point (issue #1 — extensions.py requires this)
# ---------------------------------------------------------------------------

async def setup(ctx) -> "DialecticSync":  # ctx: ExtensionContext
    """Create and return a DialecticSync, creating the forum channel if needed."""
    from bridge.extensions import ExtensionContext
    assert isinstance(ctx, ExtensionContext)

    # Look for an existing dialectic-sessions forum channel
    forum = discord.utils.get(ctx.guild.forums, name="dialectic-sessions")
    if forum is None:
        gov_cat = discord.utils.get(ctx.guild.categories, name="GOVERNANCE")
        forum = await ctx.guild.create_forum(
            name="dialectic-sessions",
            category=gov_cat,
            topic="Dialectic review sessions mirrored from UNITARES governance",
        )
        log.info("Created dialectic-sessions forum channel")

    return DialecticSync(
        gov_client=ctx.gov_client,
        cache=ctx.cache,
        forum_channel=forum,
    )
