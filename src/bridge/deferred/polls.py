"""Poll manager -- creates Discord reaction polls for governance verdicts.

Polls give humans an override window.  When no humans vote, the bridge
decides autonomously based on current EISV metrics rather than defaulting
to the conservative option.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import discord

from bridge.cache import BridgeCache
from bridge.mcp_client import GovernanceClient
from bridge.tasks import create_logged_task
from bridge.utils import parse_tool_result as _parse_tool_result_util

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLL_DURATION_MINUTES = 15
EXPIRY_CHECK_INTERVAL = 30  # seconds

# Reaction options per verdict type
PAUSE_OPTIONS: list[tuple[str, str]] = [
    ("\u2705", "Resume"),
    ("\u23f8\ufe0f", "Hold"),
    ("\U0001f504", "Dialectic"),
]

REJECT_OPTIONS: list[tuple[str, str]] = [
    ("\u2705", "Override"),
    ("\u26d4", "Uphold"),
    ("\U0001f50d", "Investigate"),
]

# Conservative (tie-breaking) option index -- always index 1
CONSERVATIVE_INDEX = 1


# ---------------------------------------------------------------------------
# Pure embed builders
# ---------------------------------------------------------------------------


def build_poll_embed(event: dict, verdict_type: str) -> discord.Embed:
    """Build the poll embed shown in #alerts.

    Parameters
    ----------
    event:
        The governance verdict_change event dict.
    verdict_type:
        Either ``"pause"`` or ``"reject"``.
    """
    agent_name = event.get("agent_name", "unknown")
    agent_id = event.get("agent_id", "?")
    reason = event.get("message", "No reason provided")
    eisv = event.get("eisv", {})

    if verdict_type == "pause":
        title = f"Agent Paused -- {agent_name}"
        colour = discord.Colour.orange()
        options = PAUSE_OPTIONS
    else:
        title = f"Agent Rejected -- {agent_name}"
        colour = discord.Colour.red()
        options = REJECT_OPTIONS

    embed = discord.Embed(title=title, description=reason, colour=colour)
    embed.add_field(name="Agent ID", value=agent_id[:12], inline=True)

    if eisv:
        embed.add_field(
            name="EISV",
            value=(
                f"E={eisv.get('E', 0):.2f} "
                f"I={eisv.get('I', 0):.2f} "
                f"S={eisv.get('S', 0):.2f} "
                f"V={eisv.get('V', 0):.2f}"
            ),
            inline=True,
        )

    option_lines = "\n".join(f"{emoji}  {label}" for emoji, label in options)
    embed.add_field(name="Vote", value=option_lines, inline=False)
    embed.set_footer(
        text=f"Poll closes in {POLL_DURATION_MINUTES} minutes. Tie = conservative option.",
    )

    return embed


def build_audit_embed(poll_result: dict) -> discord.Embed:
    """Build an embed summarising the poll outcome for #audit-log.

    Parameters
    ----------
    poll_result:
        Dict with keys: ``agent_id``, ``verdict_type``, ``winner``,
        ``vote_counts``, ``action_taken``.
    """
    winner = poll_result.get("winner", "unknown")
    action = poll_result.get("action_taken", "none")
    verdict_type = poll_result.get("verdict_type", "?")
    agent_id = poll_result.get("agent_id", "?")
    vote_counts = poll_result.get("vote_counts", {})

    embed = discord.Embed(
        title="Poll Resolved",
        description=f"Verdict: **{verdict_type}** | Winner: **{winner}**",
        colour=discord.Colour.green() if action == "resumed" else discord.Colour.light_grey(),
    )
    embed.add_field(name="Agent", value=agent_id[:12], inline=True)
    embed.add_field(name="Action", value=action, inline=True)

    if vote_counts:
        counts_str = " | ".join(f"{k}: {v}" for k, v in vote_counts.items())
        embed.add_field(name="Votes", value=counts_str, inline=False)

    return embed


# ---------------------------------------------------------------------------
# Tally helpers
# ---------------------------------------------------------------------------


def tally_votes(
    reaction_counts: dict[str, int],
    options: list[tuple[str, str]],
) -> tuple[str, dict[str, int]]:
    """Determine the winning option from reaction counts.

    Parameters
    ----------
    reaction_counts:
        Mapping of emoji -> count (bot's own reaction already subtracted).
    options:
        The ordered list of ``(emoji, label)`` pairs for this poll type.

    Returns
    -------
    (winner_label, vote_counts_by_label)
        Winner is determined by majority; ties go to the conservative option
        (index 1 -- Hold or Uphold).
    """
    vote_counts: dict[str, int] = {}
    max_votes = 0
    max_indices: list[int] = []

    for idx, (emoji, label) in enumerate(options):
        count = reaction_counts.get(emoji, 0)
        vote_counts[label] = count
        if count > max_votes:
            max_votes = count
            max_indices = [idx]
        elif count == max_votes:
            max_indices.append(idx)

    # Tie or zero votes -> conservative option
    if len(max_indices) != 1:
        winner_label = options[CONSERVATIVE_INDEX][1]
    else:
        winner_label = options[max_indices[0]][1]

    return winner_label, vote_counts


# ---------------------------------------------------------------------------
# MCP response parsing
# ---------------------------------------------------------------------------


# Consolidated to bridge.utils (issue #7).
def _parse_tool_result(result: dict | None) -> dict | list:
    return _parse_tool_result_util(result)


# ---------------------------------------------------------------------------
# PollManager
# ---------------------------------------------------------------------------


class PollManager:
    """Creates and resolves Discord reaction polls for governance verdicts."""

    def __init__(
        self,
        gov_client: GovernanceClient,
        cache: BridgeCache,
        audit_channel: discord.TextChannel | None = None,
    ) -> None:
        self.gov = gov_client
        self.cache = cache
        self.audit_channel = audit_channel
        self.bot = None  # Set by bot.py after construction
        self._expiry_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background expiry-check loop."""
        self._expiry_task = create_logged_task(self._expiry_loop(), name="poll-expiry")

    async def stop(self) -> None:
        """Cancel the expiry loop."""
        if self._expiry_task:
            self._expiry_task.cancel()

    # -- public entry point --------------------------------------------------

    async def handle_verdict_event(
        self,
        event: dict,
        alerts_channel: discord.TextChannel,
        audit_channel: discord.TextChannel,
        guild: discord.Guild,
    ) -> None:
        """Handle a verdict_change event with to=pause or to=reject.

        Creates a poll message with reactions in #alerts and saves state.
        """
        verdict_type = event.get("to", "pause")
        agent_id = event.get("agent_id", "unknown")

        embed = build_poll_embed(event, verdict_type)

        # Ping governance-council role
        council_role = discord.utils.get(guild.roles, name="governance-council")
        ping = council_role.mention if council_role else "@governance-council"

        msg = await alerts_channel.send(content=ping, embed=embed)

        # Add reaction options
        options = PAUSE_OPTIONS if verdict_type == "pause" else REJECT_OPTIONS
        for emoji, _label in options:
            try:
                await msg.add_reaction(emoji)
            except discord.HTTPException:
                log.warning("Failed to add reaction %s to poll message", emoji)

        # Persist poll state
        poll_id = str(uuid.uuid4())
        expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=POLL_DURATION_MINUTES)
        ).isoformat()

        # Store audit_channel reference for expiry resolution
        if not self.audit_channel:
            self.audit_channel = audit_channel

        await self.cache.save_poll(
            poll_id=poll_id,
            agent_id=agent_id,
            verdict_type=verdict_type,
            message_id=msg.id,
            channel_id=alerts_channel.id,
            expires_at=expires_at,
        )
        log.info(
            "Poll %s created for agent %s (%s) -- expires %s",
            poll_id, agent_id, verdict_type, expires_at,
        )

    # -- expiry loop ---------------------------------------------------------

    async def _expiry_loop(self) -> None:
        """Check every EXPIRY_CHECK_INTERVAL seconds for expired polls."""
        while True:
            try:
                await self._check_expired_polls()
            except Exception as exc:
                log.error("Poll expiry check error: %s", exc)
            await asyncio.sleep(EXPIRY_CHECK_INTERVAL)

    async def _check_expired_polls(self) -> None:
        """Resolve all polls whose expires_at has passed."""
        now = datetime.now(timezone.utc)
        active = await self.cache.get_active_polls()

        for poll in active:
            try:
                expires_at = datetime.fromisoformat(poll["expires_at"])
            except (ValueError, TypeError):
                log.warning("Bad expires_at for poll %s, resolving", poll["poll_id"])
                await self.cache.resolve_poll(poll["poll_id"])
                continue

            if now >= expires_at:
                poll_result = await self._resolve_poll(poll)
                if poll_result and self.audit_channel:
                    embed = build_audit_embed(poll_result)
                    try:
                        await self.audit_channel.send(embed=embed)
                    except discord.HTTPException as exc:
                        log.warning("Failed to post audit embed: %s", exc)

    async def _resolve_poll(self, poll: dict) -> dict | None:
        """Tally votes on a poll message and take action.

        When humans voted, their majority wins.  When no humans voted,
        the bridge decides autonomously: fetch current EISV metrics and
        auto-resume if recovered, otherwise request a dialectic review.

        Returns a poll_result dict on success, or None if resolution failed.
        """
        poll_id = poll["poll_id"]
        verdict_type = poll["verdict_type"]
        agent_id = poll["agent_id"]
        options = PAUSE_OPTIONS if verdict_type == "pause" else REJECT_OPTIONS

        # Fetch the message to read reactions
        reaction_counts = await self._fetch_reaction_counts(poll)
        total_human_votes = sum(reaction_counts.values())

        if total_human_votes > 0:
            # Humans voted -- honour their decision
            winner, vote_counts = tally_votes(reaction_counts, options)
        else:
            # No humans voted -- decide autonomously via EISV
            winner, vote_counts = await self._autonomous_decision(agent_id, verdict_type, options)

        # Determine action
        action_taken = "none"
        if winner in ("Resume", "Override"):
            result = await self.gov.call_tool(
                "operator_resume_agent", {"agent_id": agent_id},
            )
            action_taken = "resumed" if result else "resume_failed"
        elif winner in ("Dialectic", "Investigate"):
            try:
                reason = f"Autonomous poll resolution: {verdict_type} with no human votes"
                await self.gov.call_tool(
                    "request_dialectic_review",
                    {"agent_id": agent_id, "reason": reason},
                )
                action_taken = "dialectic_requested"
            except Exception as exc:
                log.warning("Dialectic request failed for %s: %s", agent_id, exc)
                action_taken = "dialectic_request_failed"
        else:
            # Hold or Uphold -- no governance action needed
            action_taken = "held" if verdict_type == "pause" else "upheld"

        poll_result = {
            "agent_id": agent_id,
            "verdict_type": verdict_type,
            "winner": winner,
            "vote_counts": vote_counts,
            "action_taken": action_taken,
        }

        log.info("Poll %s resolved: %s (action=%s)", poll_id, winner, action_taken)

        # Mark resolved in cache
        await self.cache.resolve_poll(poll_id)

        return poll_result

    async def _autonomous_decision(
        self, agent_id: str, verdict_type: str, options: list[tuple[str, str]],
    ) -> tuple[str, dict[str, int]]:
        """Make an autonomous decision based on current EISV metrics.

        Returns (winner_label, vote_counts) in the same format as tally_votes.
        vote_counts will be all zeros (no human votes).
        """
        vote_counts = {label: 0 for _, label in options}

        # Fetch current EISV
        try:
            metrics_result = await self.gov.call_tool(
                "get_governance_metrics", {"agent_id": agent_id},
            )
            metrics = _parse_tool_result(metrics_result)
            eisv = metrics.get("eisv", metrics)
        except Exception as exc:
            log.warning("Failed to fetch EISV for autonomous decision: %s", exc)
            eisv = {}

        e = eisv.get("E", 0)
        i = eisv.get("I", 0)
        s = eisv.get("S", 999)

        # Agent recovered: E > 0.5, I > 0.5, S < 1.0 -> resume/override
        if e > 0.5 and i > 0.5 and s < 1.0:
            # First option is always the resume/override option
            winner = options[0][1]
            log.info(
                "Autonomous decision for %s: %s (recovered: E=%.2f I=%.2f S=%.2f)",
                agent_id[:8], winner, e, i, s,
            )
        else:
            # Still degraded -> request dialectic (option index 2 for pause, 2 for reject)
            winner = options[2][1]  # "Dialectic" or "Investigate"
            log.info(
                "Autonomous decision for %s: %s (degraded: E=%.2f I=%.2f S=%.2f)",
                agent_id[:8], winner, e, i, s,
            )

        return winner, vote_counts

    async def _fetch_reaction_counts(self, poll: dict) -> dict[str, int]:
        """Fetch reaction counts from the Discord message.

        Returns a dict of emoji -> count (with the bot's own reaction subtracted).
        If the message is deleted or unreachable, returns empty dict.
        """
        if not self.bot:
            log.warning("PollManager has no bot reference, cannot fetch reactions")
            return {}

        try:
            channel = self.bot.get_channel(poll["channel_id"])
            if not channel:
                log.warning(
                    "Channel %d not found for poll %s",
                    poll["channel_id"], poll["poll_id"],
                )
                return {}
            message = await channel.fetch_message(poll["message_id"])
        except (discord.NotFound, discord.HTTPException) as exc:
            log.warning(
                "Could not fetch poll message %d: %s",
                poll["message_id"], exc,
            )
            return {}

        counts: dict[str, int] = {}
        for reaction in message.reactions:
            emoji_str = str(reaction.emoji)
            # Subtract 1 for the bot's own reaction
            count = max(reaction.count - 1, 0)
            counts[emoji_str] = count
        return counts


# ---------------------------------------------------------------------------
# Extension entry point (issue #1 — extensions.py requires this)
# ---------------------------------------------------------------------------

async def setup(ctx) -> "PollManager":  # ctx: ExtensionContext
    """Create and return a PollManager wired to ctx.

    The bot reference (needed to fetch reaction messages) is set here —
    previously it was left as None and never wired (issue #2).
    """
    from bridge.extensions import ExtensionContext
    assert isinstance(ctx, ExtensionContext)
    manager = PollManager(
        gov_client=ctx.gov_client,
        cache=ctx.cache,
        audit_channel=ctx.channels.get("audit-log"),
    )
    # Wire bot reference so _fetch_reaction_counts can look up channels
    manager.bot = ctx.bot
    return manager
