"""Autonomous governance engine -- auto-resume, auto-dialectic, neighbor warnings.

Monitors governance state and takes action without waiting for human votes.
All autonomous actions are logged to #audit-log.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import discord

from bridge.mcp_client import GovernanceClient
from bridge.cache import BridgeCache
from bridge.tasks import create_logged_task
from bridge.utils import parse_tool_result as _parse_tool_result_util

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Recovery thresholds — agent is "recovered" when these hold
# ---------------------------------------------------------------------------

RECOVERY_E_MIN = 0.5   # Ethical alignment above 0.5
RECOVERY_I_MIN = 0.5   # Integrity above 0.5
RECOVERY_S_MAX = 1.0   # Stability below 1.0 (high S = high instability)

RISK_WARNING_THRESHOLD = 0.70  # 70% risk triggers neighbor warning


# ---------------------------------------------------------------------------
# Pure embed builders
# ---------------------------------------------------------------------------


def build_auto_resume_embed(agent_id: str, metrics: dict) -> discord.Embed:
    """Green embed — agent auto-resumed after EISV recovery."""
    eisv = metrics.get("eisv", metrics)
    embed = discord.Embed(
        title="Auto-Resumed",
        description=f"Agent `{agent_id[:12]}` recovered and was automatically resumed.",
        colour=discord.Colour.green(),
    )
    embed.add_field(
        name="EISV at Resume",
        value=(
            f"E={eisv.get('E', 0):.2f}  "
            f"I={eisv.get('I', 0):.2f}  "
            f"S={eisv.get('S', 0):.2f}  "
            f"V={eisv.get('V', 0):.2f}"
        ),
        inline=False,
    )
    embed.set_footer(text="Autonomous governance action")
    return embed


def build_auto_dialectic_embed(agent_id: str, reason: str) -> discord.Embed:
    """Gold embed — dialectic review automatically requested."""
    embed = discord.Embed(
        title="Auto-Dialectic Requested",
        description=f"Dialectic review requested for `{agent_id[:12]}`.",
        colour=discord.Colour.gold(),
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="Autonomous governance action")
    return embed


def build_neighbor_warning_embed(risky_agent: str, risk_value: float) -> discord.Embed:
    """Orange embed — warns nearby agents of elevated risk."""
    embed = discord.Embed(
        title="Neighbor Risk Warning",
        description=f"Agent `{risky_agent[:12]}` has elevated risk.",
        colour=discord.Colour.orange(),
    )
    embed.add_field(name="Risk", value=f"{risk_value:.0%}", inline=True)
    embed.add_field(
        name="Advisory",
        value="Consider increasing caution in shared contexts.",
        inline=False,
    )
    embed.set_footer(text="Informational — no automatic action taken")
    return embed


def build_audit_action_embed(
    action: str, agent_id: str, reason: str, result: str,
) -> discord.Embed:
    """Audit log embed for any autonomous governance action."""
    embed = discord.Embed(
        title=f"Autonomous Action: {action}",
        colour=discord.Colour.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Agent", value=agent_id[:12], inline=True)
    embed.add_field(name="Result", value=result, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    return embed


# ---------------------------------------------------------------------------
# MCP response parsing (same pattern as dialectic.py)
# ---------------------------------------------------------------------------


# Consolidated to bridge.utils (issue #7); kept as a local alias for the
# existing call-sites in this module.
def _parse_tool_result(result: dict | None) -> dict | list:
    return _parse_tool_result_util(result)


def _is_recovered(eisv: dict) -> bool:
    """Check whether EISV metrics indicate the agent has recovered."""
    e = eisv.get("E", 0)
    i = eisv.get("I", 0)
    s = eisv.get("S", 999)
    return e > RECOVERY_E_MIN and i > RECOVERY_I_MIN and s < RECOVERY_S_MAX


# ---------------------------------------------------------------------------
# AutonomyEngine
# ---------------------------------------------------------------------------


class AutonomyEngine:
    """Background engine for autonomous governance actions.

    - Auto-resume: periodically checks paused agents and resumes if recovered.
    - Auto-dialectic: on verdict_change or critical drift, requests dialectic review.
    - Neighbor watch: on risk_threshold, warns other active agent channels.
    """

    def __init__(
        self,
        gov_client: GovernanceClient,
        cache: BridgeCache,
        alerts_channel: discord.TextChannel | None = None,
        audit_channel: discord.TextChannel | None = None,
        interval: int = 60,
    ) -> None:
        self.gov = gov_client
        self.cache = cache
        self.alerts_channel = alerts_channel
        self.audit_channel = audit_channel
        self.interval = interval
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the auto-resume monitoring loop."""
        self._task = create_logged_task(self._auto_resume_loop(), name="autonomy-engine")

    async def stop(self) -> None:
        """Cancel background tasks."""
        if self._task:
            self._task.cancel()

    # -- Event handlers (called by EventPoller) ----------------------------

    async def handle_verdict_event(self, event: dict) -> None:
        """Called by EventPoller on verdict_change to pause/reject.

        Automatically requests a dialectic review for the affected agent.
        """
        agent_id = event.get("agent_id", "unknown")
        verdict = event.get("to", "pause")
        reason = event.get("message", f"Verdict changed to {verdict}")

        log.info("Auto-dialectic for agent %s (verdict: %s)", agent_id[:8], verdict)

        try:
            result = await self.gov.call_tool(
                "request_dialectic_review",
                {"agent_id": agent_id, "reason": reason},
            )
            result_str = "requested" if result else "failed"
        except Exception as exc:
            log.warning("Auto-dialectic call failed for %s: %s", agent_id[:8], exc)
            result_str = f"error: {exc}"

        # Post to #alerts
        if self.alerts_channel:
            embed = build_auto_dialectic_embed(agent_id, reason)
            try:
                await self.alerts_channel.send(embed=embed)
            except discord.HTTPException as exc:
                log.warning("Failed to send auto-dialectic embed: %s", exc)

        # Audit log
        await self._audit("auto-dialectic", agent_id, reason, result_str)

    async def handle_drift_event(self, event: dict) -> None:
        """Called by EventPoller on critical drift_alert.

        Automatically requests a dialectic review for the drifting agent.
        """
        agent_id = event.get("agent_id", "unknown")
        axis = event.get("axis", "unknown")
        value = event.get("value", 0)
        reason = f"Critical drift on axis={axis} (value={value:.2f})"

        log.info("Auto-dialectic for drift: agent %s, %s", agent_id[:8], reason)

        try:
            result = await self.gov.call_tool(
                "request_dialectic_review",
                {"agent_id": agent_id, "reason": reason},
            )
            result_str = "requested" if result else "failed"
        except Exception as exc:
            log.warning("Auto-dialectic (drift) failed for %s: %s", agent_id[:8], exc)
            result_str = f"error: {exc}"

        # Audit log
        await self._audit("auto-dialectic-drift", agent_id, reason, result_str)

    async def handle_risk_event(self, event: dict, presence_manager) -> None:
        """Called by EventPoller on risk_threshold.

        Posts a warning to all other active agent channels.
        """
        risky_agent = event.get("agent_id", "unknown")
        risk_value = event.get("value", 0)

        if risk_value < RISK_WARNING_THRESHOLD:
            return

        log.info("Neighbor warning: agent %s at %.0f%% risk", risky_agent[:8], risk_value * 100)

        embed = build_neighbor_warning_embed(risky_agent, risk_value)

        # Get all agent channels from the AGENTS category via presence_manager
        warned_count = 0
        if presence_manager and hasattr(presence_manager, "agents_category"):
            for channel in presence_manager.agents_category.channels:
                # Skip the risky agent's own channel
                if risky_agent[:8] in (channel.topic or ""):
                    continue
                if not isinstance(channel, discord.TextChannel):
                    continue
                try:
                    await channel.send(embed=embed)
                    warned_count += 1
                except discord.HTTPException as exc:
                    log.warning("Failed to warn channel %s: %s", channel.name, exc)

        # Audit log
        await self._audit(
            "neighbor-warning",
            risky_agent,
            f"Risk at {risk_value:.0%}, warned {warned_count} channels",
            f"warned {warned_count} neighbors",
        )

    # -- Auto-resume loop --------------------------------------------------

    async def _auto_resume_loop(self) -> None:
        """Check for recoverable paused agents every interval seconds."""
        while True:
            try:
                await self._check_paused_agents()
            except Exception as exc:
                log.error("Auto-resume loop error: %s", exc)
            await asyncio.sleep(self.interval)

    async def _check_paused_agents(self) -> None:
        """Fetch all agents, check paused ones for recovery, auto-resume if recovered."""
        result = await self.gov.call_tool("list_agents", {})
        agents = _parse_tool_result(result)

        if isinstance(agents, dict):
            agents = agents.get("agents", [])

        for agent in agents:
            verdict = agent.get("verdict", "")
            if verdict != "pause":
                continue

            agent_id = agent.get("agent_id", "")
            if not agent_id:
                continue

            # Fetch current EISV metrics
            metrics_result = await self.gov.call_tool(
                "get_governance_metrics", {"agent_id": agent_id},
            )
            metrics = _parse_tool_result(metrics_result)
            eisv = metrics.get("eisv", metrics)

            if not _is_recovered(eisv):
                log.debug(
                    "Agent %s still degraded (E=%.2f, I=%.2f, S=%.2f)",
                    agent_id[:8],
                    eisv.get("E", 0), eisv.get("I", 0), eisv.get("S", 0),
                )
                continue

            # Agent has recovered -- auto-resume
            log.info("Auto-resuming agent %s (recovered)", agent_id[:8])
            try:
                resume_result = await self.gov.call_tool(
                    "operator_resume_agent", {"agent_id": agent_id},
                )
                result_str = "resumed" if resume_result else "resume_failed"
            except Exception as exc:
                log.warning("Auto-resume call failed for %s: %s", agent_id[:8], exc)
                result_str = f"error: {exc}"

            # Post to #alerts
            if self.alerts_channel:
                embed = build_auto_resume_embed(agent_id, eisv)
                try:
                    await self.alerts_channel.send(embed=embed)
                except discord.HTTPException as exc:
                    log.warning("Failed to send auto-resume embed: %s", exc)

            # Audit log
            await self._audit(
                "auto-resume",
                agent_id,
                f"EISV recovered: E={eisv.get('E', 0):.2f} I={eisv.get('I', 0):.2f} S={eisv.get('S', 0):.2f}",
                result_str,
            )

    # -- Audit helper ------------------------------------------------------

    async def _audit(self, action: str, agent_id: str, reason: str, result: str) -> None:
        """Post an audit action embed to #audit-log."""
        if not self.audit_channel:
            log.info("Audit (no channel): %s agent=%s reason=%s result=%s", action, agent_id[:8], reason, result)
            return
        embed = build_audit_action_embed(action, agent_id, reason, result)
        try:
            await self.audit_channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.warning("Failed to send audit embed: %s", exc)


# ---------------------------------------------------------------------------
# Extension entry point (issue #1 — extensions.py requires this)
# ---------------------------------------------------------------------------

async def setup(ctx) -> "AutonomyEngine":  # ctx: ExtensionContext
    """Create and return an AutonomyEngine wired to ctx."""
    from bridge.extensions import ExtensionContext  # avoid circular at import time
    assert isinstance(ctx, ExtensionContext)
    return AutonomyEngine(
        gov_client=ctx.gov_client,
        cache=ctx.cache,
        alerts_channel=ctx.channels.get("alerts"),
        audit_channel=ctx.channels.get("audit-log"),
    )
