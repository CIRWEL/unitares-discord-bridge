"""Map governance events to Discord embeds."""

from __future__ import annotations

import discord

SEVERITY_COLOURS = {
    "info": discord.Colour.blue(),
    "warning": discord.Colour.orange(),
    "critical": discord.Colour.red(),
}

EVENT_TITLES = {
    "agent_new": "New Agent",
    "verdict_change": "Verdict Change",
    "risk_threshold": "Risk Threshold",
    "drift_alert": "Drift Alert",
    "drift_oscillation": "Drift Oscillation",
    "trajectory_adjustment": "Trajectory Adjustment",
    "agent_idle": "Agent Idle",
}


def event_to_embed(event: dict) -> discord.Embed:
    """Convert a governance event dict to a Discord embed."""
    severity = event.get("severity", "info")
    event_type = event.get("type", "unknown")
    colour = SEVERITY_COLOURS.get(severity, discord.Colour.greyple())
    title = EVENT_TITLES.get(event_type, event_type.replace("_", " ").title())

    embed = discord.Embed(
        title=title,
        description=event.get("message", ""),
        colour=colour,
    )
    embed.add_field(name="Agent", value=event.get("agent_name", "unknown"), inline=True)
    embed.add_field(name="Severity", value=severity, inline=True)

    # Type-specific fields
    if event_type == "verdict_change":
        embed.add_field(
            name="Transition",
            value=f"{event.get('from', '?')} \u2192 {event.get('to', '?')}",
            inline=False,
        )
    elif event_type == "risk_threshold":
        embed.add_field(name="Risk", value=f"{event.get('value', 0):.0%}", inline=True)
        embed.add_field(name="Direction", value=event.get("direction", "?"), inline=True)
    elif event_type == "drift_alert":
        embed.add_field(name="Axis", value=event.get("axis", "?"), inline=True)
        embed.add_field(name="Value", value=f"{event.get('value', 0):.2f}", inline=True)

    embed.set_footer(text=f"Event #{event.get('event_id', '?')}")
    return embed


def is_critical_event(event: dict) -> bool:
    """Should this event also be posted to #alerts?"""
    if event.get("severity") == "critical":
        return True
    if event.get("type") == "verdict_change" and event.get("to") in ("pause", "reject"):
        return True
    return False
