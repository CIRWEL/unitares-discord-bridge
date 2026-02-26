"""Resonance threads -- watch CIRS resonance events and manage Discord threads."""

from __future__ import annotations

import logging

import discord

log = logging.getLogger(__name__)

# CIRS event types that this module handles
CIRS_EVENT_TYPES = frozenset({
    "RESONANCE_ALERT",
    "STATE_ANNOUNCE",
    "COHERENCE_REPORT",
    "STABILITY_RESTORED",
})


# ---------------------------------------------------------------------------
# Embed builders (pure functions, easily testable)
# ---------------------------------------------------------------------------

def build_resonance_alert_embed(event: dict) -> discord.Embed:
    """Opening embed when resonance is detected between two agents. Gold."""
    agent_a = event.get("agent_a_name", event.get("agent_a", "?"))
    agent_b = event.get("agent_b_name", event.get("agent_b", "?"))
    embed = discord.Embed(
        title=f"Resonance Detected: {agent_a} \u2194 {agent_b}",
        description=event.get("message", "Coupling detected between agents"),
        colour=discord.Colour.gold(),
    )
    embed.add_field(name="Agent A", value=agent_a, inline=True)
    embed.add_field(name="Agent B", value=agent_b, inline=True)
    severity = event.get("severity", "info")
    embed.add_field(name="Severity", value=severity, inline=True)
    if event.get("coupling_strength") is not None:
        embed.add_field(
            name="Coupling Strength",
            value=f"{event['coupling_strength']:.2f}",
            inline=True,
        )
    return embed


def build_state_update_embed(event: dict) -> discord.Embed:
    """Agent state update posted into an active resonance thread. Blue."""
    agent_name = event.get("agent_name", event.get("agent_id", "?"))
    embed = discord.Embed(
        title=f"State Update: {agent_name}",
        description=event.get("message", ""),
        colour=discord.Colour.blue(),
    )
    embed.add_field(name="Agent", value=agent_name, inline=True)
    state = event.get("state", {})
    if isinstance(state, dict):
        for key, val in state.items():
            embed.add_field(name=key.replace("_", " ").title(), value=str(val), inline=True)
    return embed


def build_coherence_embed(event: dict) -> discord.Embed:
    """Pairwise coherence metrics between two agents. Purple."""
    agent_a = event.get("agent_a_name", event.get("agent_a", "?"))
    agent_b = event.get("agent_b_name", event.get("agent_b", "?"))
    embed = discord.Embed(
        title=f"Coherence Report: {agent_a} \u2194 {agent_b}",
        description=event.get("message", ""),
        colour=discord.Colour.purple(),
    )
    metrics = event.get("metrics", {})
    if isinstance(metrics, dict):
        for key, val in metrics.items():
            if isinstance(val, (int, float)):
                embed.add_field(name=key.replace("_", " ").title(), value=f"{val:.3f}", inline=True)
            else:
                embed.add_field(name=key.replace("_", " ").title(), value=str(val), inline=True)
    if event.get("coherence") is not None:
        embed.add_field(name="Overall Coherence", value=f"{event['coherence']:.3f}", inline=False)
    return embed


def build_stability_embed(event: dict) -> discord.Embed:
    """Resolution embed when stability is restored. Green."""
    agent_a = event.get("agent_a_name", event.get("agent_a", "?"))
    agent_b = event.get("agent_b_name", event.get("agent_b", "?"))
    embed = discord.Embed(
        title=f"Stability Restored: {agent_a} \u2194 {agent_b}",
        description=event.get("message", "Resonance resolved, agents stable"),
        colour=discord.Colour.green(),
    )
    embed.add_field(name="Agent A", value=agent_a, inline=True)
    embed.add_field(name="Agent B", value=agent_b, inline=True)
    if event.get("duration"):
        embed.add_field(name="Duration", value=event["duration"], inline=True)
    return embed


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _resonance_key(agent_a: str, agent_b: str) -> str:
    """Consistent key for an agent pair (sorted, joined with '-')."""
    return "-".join(sorted([agent_a, agent_b]))


def _extract_agent_ids(event: dict) -> tuple[str, str] | None:
    """Extract the two agent IDs from a CIRS event, or None if not found."""
    a = event.get("agent_a") or event.get("agent_a_id") or event.get("agent_id")
    b = event.get("agent_b") or event.get("agent_b_id")
    if a and b:
        return (a, b)
    return None


# ---------------------------------------------------------------------------
# Resonance Tracker
# ---------------------------------------------------------------------------

class ResonanceTracker:
    """Track CIRS resonance events and manage Discord threads in #resonance."""

    def __init__(self, resonance_channel: discord.TextChannel) -> None:
        self.channel = resonance_channel
        self._active_threads: dict[str, discord.Thread] = {}

    async def handle_event(self, event: dict) -> None:
        """Dispatch a CIRS event to the appropriate handler."""
        event_type = event.get("type", "")
        if event_type == "RESONANCE_ALERT":
            await self._handle_resonance_alert(event)
        elif event_type == "STATE_ANNOUNCE":
            await self._handle_state_announce(event)
        elif event_type == "COHERENCE_REPORT":
            await self._handle_coherence_report(event)
        elif event_type == "STABILITY_RESTORED":
            await self._handle_stability_restored(event)

    async def _handle_resonance_alert(self, event: dict) -> None:
        """Create a new thread for a resonance alert."""
        pair = _extract_agent_ids(event)
        if not pair:
            log.warning("RESONANCE_ALERT missing agent pair: %s", event)
            return

        key = _resonance_key(*pair)

        # Don't create duplicate threads for the same pair
        if key in self._active_threads:
            log.info("Thread already active for pair %s, posting update", key)
            embed = build_resonance_alert_embed(event)
            try:
                await self._active_threads[key].send(embed=embed)
            except discord.HTTPException as exc:
                log.warning("Failed to post to existing thread: %s", exc)
            return

        agent_a = event.get("agent_a_name", event.get("agent_a", "?"))
        agent_b = event.get("agent_b_name", event.get("agent_b", "?"))
        thread_name = f"Resonance: {agent_a} \u2194 {agent_b}"

        embed = build_resonance_alert_embed(event)
        try:
            # Create a public thread in #resonance with an opening message
            msg = await self.channel.send(embed=embed)
            thread = await msg.create_thread(name=thread_name[:100])
            self._active_threads[key] = thread
            log.info("Created resonance thread: %s", thread_name)
        except discord.HTTPException as exc:
            log.error("Failed to create resonance thread: %s", exc)

    async def _handle_state_announce(self, event: dict) -> None:
        """Post a state update to any active thread involving this agent."""
        agent_id = event.get("agent_id", "")
        if not agent_id:
            return

        embed = build_state_update_embed(event)
        posted = False
        for key, thread in self._active_threads.items():
            if agent_id in key.split("-"):
                try:
                    await thread.send(embed=embed)
                    posted = True
                except discord.HTTPException as exc:
                    log.warning("Failed to post state update to thread: %s", exc)

        if not posted:
            log.debug("No active resonance thread for agent %s", agent_id)

    async def _handle_coherence_report(self, event: dict) -> None:
        """Post coherence metrics to the relevant resonance thread."""
        pair = _extract_agent_ids(event)
        if not pair:
            return

        key = _resonance_key(*pair)
        thread = self._active_threads.get(key)
        if not thread:
            log.debug("No active thread for coherence report pair %s", key)
            return

        embed = build_coherence_embed(event)
        try:
            await thread.send(embed=embed)
        except discord.HTTPException as exc:
            log.warning("Failed to post coherence report: %s", exc)

    async def _handle_stability_restored(self, event: dict) -> None:
        """Post resolution embed and archive the thread."""
        pair = _extract_agent_ids(event)
        if not pair:
            return

        key = _resonance_key(*pair)
        thread = self._active_threads.get(key)
        if not thread:
            log.debug("No active thread for stability restored pair %s", key)
            return

        embed = build_stability_embed(event)
        try:
            await thread.send(embed=embed)
            await thread.edit(archived=True)
            log.info("Archived resonance thread: %s", thread.name)
        except discord.HTTPException as exc:
            log.warning("Failed to archive resonance thread: %s", exc)
        finally:
            self._active_threads.pop(key, None)
