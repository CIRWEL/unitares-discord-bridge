"""Discord slash commands for governance interaction."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands

from bridge.mcp_client import GovernanceClient, AnimaClient, parse_tool_result, fetch_agents, fetch_metrics

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response builders (pure functions, testable without Discord)
# ---------------------------------------------------------------------------

def build_status_embed(agents: list[dict], metrics: dict[str, dict]) -> discord.Embed:
    """Build an EISV status overview embed for all agents."""
    from bridge.hud import build_hud_embed
    return build_hud_embed(agents, metrics)


def build_kg_search_embed(query: str, results: list[dict]) -> discord.Embed:
    """Build an embed for a knowledge graph search.

    Pure function — takes the parsed search results dict list and renders.
    """
    if not results:
        embed = discord.Embed(
            title=f"Knowledge: '{query}'",
            description="No discoveries matched.",
            colour=discord.Colour.greyple(),
            timestamp=datetime.now(timezone.utc),
        )
        return embed

    embed = discord.Embed(
        title=f"Knowledge: '{query}' ({len(results)} result{'s' if len(results) != 1 else ''})",
        colour=discord.Colour.blurple(),
        timestamp=datetime.now(timezone.utc),
    )

    # Discord embed field limits: 25 fields, 1024 chars per value. Cap at 5
    # results to keep the reply compact and leave headroom for truncation.
    for d in results[:5]:
        author = d.get("by") or d.get("_agent_id", "unknown")
        summary = (d.get("summary") or "").strip()
        dtype = d.get("type", "note")
        tags = d.get("tags", []) or []
        tag_str = " ".join(f"`{t}`" for t in tags[:4]) if tags else ""
        created = d.get("created_at", "")
        # Discord field value cap is 1024 chars
        body_parts = []
        if summary:
            body_parts.append(summary[:700] + ("…" if len(summary) > 700 else ""))
        meta_bits = []
        if dtype and dtype != "note":
            meta_bits.append(f"type: {dtype}")
        if tag_str:
            meta_bits.append(tag_str)
        if created:
            meta_bits.append(created.split("T")[0] if "T" in created else created)
        if meta_bits:
            body_parts.append("  •  ".join(meta_bits))
        value = "\n".join(body_parts) or "—"
        embed.add_field(name=f"{author}", value=value[:1024], inline=False)

    if len(results) > 5:
        embed.set_footer(text=f"showing 5 of {len(results)} matches")
    return embed


def build_agent_embed(agent_id: str, data: dict) -> discord.Embed:
    """Build a detailed agent view embed."""
    label = data.get("label") or data.get("name") or agent_id
    verdict = data.get("verdict", "unknown")
    embed = discord.Embed(
        title=f"Agent: {label}",
        colour=_verdict_colour(verdict),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="ID", value=agent_id[:12], inline=True)
    embed.add_field(name="Verdict", value=verdict, inline=True)
    embed.add_field(
        name="EISV",
        value=(
            f"E={data.get('E', 0.0):.2f}  I={data.get('I', 0.0):.2f}  "
            f"S={data.get('S', 0.0):.2f}  V={data.get('V', 0.0):.2f}"
        ),
        inline=False,
    )
    if data.get("trajectory"):
        embed.add_field(name="Trajectory", value=str(data["trajectory"]), inline=False)
    if data.get("last_seen"):
        embed.add_field(name="Last Seen", value=str(data["last_seen"]), inline=True)
    return embed


def build_health_embed(health: dict) -> discord.Embed:
    """Build a system health embed from /health response."""
    status = health.get("status", "unknown")
    colour = discord.Colour.green() if status == "ok" else discord.Colour.red()
    embed = discord.Embed(
        title="System Health",
        colour=colour,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Status", value=status, inline=True)

    # Uptime may be a dict {"seconds": N, "formatted": "Xm Ys"} or a string
    uptime = health.get("uptime", "?")
    if isinstance(uptime, dict):
        uptime = uptime.get("formatted", f"{uptime.get('seconds', '?')}s")
    embed.add_field(name="Uptime", value=str(uptime), inline=True)

    # Connection count from the connections block
    connections = health.get("connections", {})
    if isinstance(connections, dict):
        embed.add_field(
            name="Connections", value=str(connections.get("active", "?")), inline=True,
        )

    # Database status may be a dict {"status": "connected", ...} or a string
    db = health.get("database", health.get("db", "?"))
    if isinstance(db, dict):
        db = db.get("status", "?")
    embed.add_field(name="Database", value=str(db), inline=True)

    version = health.get("version", "")
    if version:
        embed.set_footer(text=f"v{version}")
    return embed


def build_resume_embed(agent_id: str, result: dict) -> discord.Embed:
    """Build a resume confirmation embed."""
    success = result.get("success", result.get("resumed", False))
    colour = discord.Colour.green() if success else discord.Colour.red()
    embed = discord.Embed(
        title="Agent Resume",
        colour=colour,
        timestamp=datetime.now(timezone.utc),
    )
    status_text = "Resumed" if success else "Failed"
    embed.add_field(name="Agent", value=agent_id[:12], inline=True)
    embed.add_field(name="Status", value=status_text, inline=True)
    message = result.get("message", result.get("reason", ""))
    if message:
        embed.description = message
    return embed


def build_lumen_embed(state: dict) -> discord.Embed:
    """Build a Lumen state embed from anima /state response."""
    from bridge.lumen import build_sensor_embed
    return build_sensor_embed(state)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verdict_colour(verdict: str) -> discord.Colour:
    return {
        "proceed": discord.Colour.green(),
        "guide": discord.Colour.gold(),
        "pause": discord.Colour.red(),
        "reject": discord.Colour.dark_red(),
    }.get(verdict, discord.Colour.greyple())




# ---------------------------------------------------------------------------
# Slash command setup
# ---------------------------------------------------------------------------

def setup_commands(
    bot: discord.ext.commands.Bot,
    gov_client: GovernanceClient,
    anima_client: AnimaClient,
) -> None:
    """Register slash commands on the bot's command tree."""

    tree = bot.tree

    @tree.command(name="status", description="Show current EISV for all agents")
    async def cmd_status(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            agents = await fetch_agents(gov_client)
            metrics = await fetch_metrics(gov_client, agents)
            embed = build_status_embed(agents, metrics)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/status error: %s", exc)
            await interaction.followup.send(
                embed=_error_embed("Failed to fetch agent status."),
            )

    @tree.command(name="agent", description="Detailed view of one agent's metrics")
    @app_commands.describe(name="Agent ID or name")
    async def cmd_agent(interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer()
        try:
            # observe_agent is case-sensitive on label; resolve via list_agents first
            resolved = await _resolve_agent_label(gov_client, name)
            if resolved is None:
                await interaction.followup.send(
                    embed=_error_embed(f"Agent '{name}' not found."),
                )
                return

            result = await gov_client.call_tool(
                "observe_agent", {"agent_id": resolved["label"]},
            )
            if not result:
                await interaction.followup.send(
                    embed=_error_embed("Governance unavailable."),
                )
                return
            data = parse_tool_result(result)
            if isinstance(data, list):
                data = data[0] if data else {}

            # observe_agent returns {"observation": {"current_state": {E, I, S, V, ...}}}
            state = data.get("observation", {}).get("current_state", {})
            flat = {
                "label": resolved["label"],
                "E": state.get("E", 0.0),
                "I": state.get("I", 0.0),
                "S": state.get("S", 0.0),
                "V": state.get("V", 0.0),
                "verdict": _state_to_verdict(state),
                "last_seen": data.get("server_time"),
            }
            agent_id = data.get("agent_id") or resolved["id"]
            embed = build_agent_embed(agent_id, flat)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/agent error: %s", exc)
            await interaction.followup.send(
                embed=_error_embed(f"Failed to fetch agent '{name}'."),
            )

    @tree.command(name="health", description="System health check")
    async def cmd_health(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            health = await gov_client.fetch_health()
            if not health:
                await interaction.followup.send(
                    embed=_error_embed("Governance service unreachable."),
                )
                return
            embed = build_health_embed(health)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/health error: %s", exc)
            await interaction.followup.send(
                embed=_error_embed("Health check failed."),
            )

    @tree.command(name="resume", description="Resume a paused agent")
    @app_commands.describe(agent="Agent ID to resume")
    async def cmd_resume(interaction: discord.Interaction, agent: str) -> None:
        # Guard: only users with Manage Server permission or the 'Governance Admin'
        # role may resume agents.  Check before deferring so we can send ephemeral.
        has_permission = interaction.permissions.manage_guild or any(
            r.name == "Governance Admin"
            for r in getattr(interaction.user, "roles", [])
        )
        if not has_permission:
            await interaction.response.send_message(
                embed=_error_embed(
                    "You need the **Governance Admin** role or **Manage Server** "
                    "permission to resume agents."
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        try:
            result = await gov_client.call_tool(
                "operator_resume_agent", {"agent_id": agent},
            )
            if not result:
                await interaction.followup.send(
                    embed=_error_embed(f"Failed to resume agent '{agent}'."),
                )
                return
            data = parse_tool_result(result)
            if isinstance(data, list):
                data = data[0] if data else {}
            embed = build_resume_embed(agent, data)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/resume error: %s", exc)
            await interaction.followup.send(
                embed=_error_embed(f"Resume failed for '{agent}'."),
            )

    @tree.command(name="kg", description="Search the shared knowledge graph")
    @app_commands.describe(query="Search text — substring or tag-style match")
    async def cmd_kg(interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer()
        q = query.strip()
        if not q:
            await interaction.followup.send(
                embed=_error_embed("Empty query — pass some search text."),
            )
            return
        try:
            raw = await gov_client.call_tool(
                "knowledge",
                {
                    "action": "search",
                    "query": q,
                    "limit": 10,
                    # Force the fast indexed-filter path — the semantic
                    # auto-detect can hang under MCP session contention
                    # (see sdk-design doc, deployment constraints section).
                    "semantic": False,
                },
            )
            if not raw:
                await interaction.followup.send(
                    embed=_error_embed("Governance unavailable."),
                )
                return
            data = parse_tool_result(raw)
            if isinstance(data, list):
                data = data[0] if data else {}
            # search returns {"discoveries": [...]} wrapped inside the tool result
            results = data.get("discoveries") or data.get("results") or []
            embed = build_kg_search_embed(q, results)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/kg error: %s", exc)
            await interaction.followup.send(
                embed=_error_embed("Knowledge graph search failed."),
            )

    @tree.command(name="lumen", description="Current Lumen state and sensors")
    async def cmd_lumen(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            state = await anima_client.fetch_state()
            if not state:
                await interaction.followup.send(
                    embed=_error_embed("Lumen is offline."),
                )
                return
            embed = build_lumen_embed(state)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            log.error("/lumen error: %s", exc)
            await interaction.followup.send(
                embed=_error_embed("Failed to fetch Lumen state."),
            )


def _error_embed(message: str) -> discord.Embed:
    """Build a red error embed."""
    return discord.Embed(
        title="Error",
        description=message,
        colour=discord.Colour.red(),
    )


async def _resolve_agent_label(
    gov_client: GovernanceClient, query: str,
) -> dict | None:
    """Resolve a user-typed label or id to {"id", "label"} via list_agents.

    observe_agent is case-sensitive on label, so we do the case-insensitive
    matching ourselves here.
    """
    agents = await fetch_agents(gov_client)
    q = query.strip().lower()
    for agent in agents:
        if agent["label"].lower() == q or agent["id"] == query:
            return agent
    # Loose contains match as a fallback
    for agent in agents:
        if q in agent["label"].lower():
            return agent
    return None


def _state_to_verdict(state: dict) -> str:
    """Map an observe_agent current_state dict to a HUD verdict."""
    risk = float(state.get("risk_score", 0.0))
    coherence = float(state.get("coherence", 0.0))
    if risk >= 0.75:
        return "pause"
    if risk >= 0.5 or coherence < 0.4:
        return "guide"
    if coherence >= 0.5:
        return "proceed"
    return "guide"


