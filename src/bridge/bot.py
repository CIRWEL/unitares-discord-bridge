import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands

from bridge.config import (
    DISCORD_TOKEN, GUILD_ID, GOVERNANCE_URL, ANIMA_URL,
    GOVERNANCE_TOKEN, ANIMA_TOKEN,
    EVENT_POLL_INTERVAL, HUD_UPDATE_INTERVAL, SENSOR_POLL_INTERVAL,
    DRAWING_POLL_INTERVAL, DB_PATH,
    BRIDGE_EXTENSIONS,
)
from bridge.cache import BridgeCache
from bridge.mcp_client import GovernanceClient, AnimaClient
from bridge.server_setup import ensure_server_structure
from bridge.event_poller import EventPoller
from bridge.hud import HUDUpdater
from bridge.lumen import LumenPoller
from bridge.commands import setup_commands
from bridge.extensions import Extension, ExtensionContext, load_extensions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("bridge")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

gov_client = GovernanceClient(GOVERNANCE_URL, token=GOVERNANCE_TOKEN)
anima_client = AnimaClient(ANIMA_URL, token=ANIMA_TOKEN)

cache: BridgeCache | None = None
event_poller: EventPoller | None = None
hud_updater: HUDUpdater | None = None
lumen_poller: LumenPoller | None = None
audit_channel: discord.TextChannel | None = None
_extensions: list[Extension] = []
_initialized: bool = False


@bot.event
async def on_ready():
    global cache, event_poller, hud_updater, lumen_poller, audit_channel, _extensions, _initialized

    if _initialized:
        log.info("Reconnected as %s (skipping re-init)", bot.user)
        return

    log.info("Bridge online as %s", bot.user)
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        log.error("Guild %d not found", GUILD_ID)
        return

    channels = await ensure_server_structure(guild)
    log.info("Server structure ready: %d channels", len(channels))

    # Open persistent HTTP clients
    await gov_client.open()
    await anima_client.open()

    # Open the SQLite cache for event cursors, HUD state, etc.
    os.makedirs(os.path.dirname(DB_PATH) or "data", exist_ok=True)
    cache = BridgeCache(DB_PATH)
    await cache.__aenter__()

    # Start the event poller if both channels exist
    events_ch = channels.get("events")
    alerts_ch = channels.get("alerts")
    audit_channel = channels.get("audit-log")
    if events_ch and alerts_ch:
        event_poller = EventPoller(
            gov_client, cache, events_ch, alerts_ch, EVENT_POLL_INTERVAL,
            audit_channel=audit_channel,
        )
        await event_poller.start()
        log.info("Event poller started")

    # Start the HUD updater if the channel exists
    hud_ch = channels.get("governance-hud")
    if hud_ch:
        hud_updater = HUDUpdater(
            gov_client, cache, hud_ch, HUD_UPDATE_INTERVAL,
            anima_client=anima_client,
        )
        await hud_updater.start()
        log.info("HUD updater started")

    # Start the Lumen poller if all three channels exist
    stream_ch = channels.get("lumen-stream")
    art_ch = channels.get("lumen-art")
    sensor_ch = channels.get("lumen-sensors")
    if stream_ch and art_ch and sensor_ch:
        lumen_poller = LumenPoller(
            anima_client, stream_ch, art_ch, sensor_ch,
            sensor_interval=SENSOR_POLL_INTERVAL,
            drawing_interval=DRAWING_POLL_INTERVAL,
        )
        await lumen_poller.start()
        log.info("Lumen poller started")

    # Sync the slash command tree
    try:
        await bot.tree.sync()
        log.info("Slash command tree synced")
    except Exception as exc:
        log.error("Failed to sync command tree: %s", exc)

    # Load deferred extensions if configured
    if BRIDGE_EXTENSIONS:
        ctx = ExtensionContext(
            guild=guild,
            channels=channels,
            gov_client=gov_client,
            anima_client=anima_client,
            cache=cache,
            bot=bot,
        )
        _extensions = await load_extensions(BRIDGE_EXTENSIONS, ctx)
        log.info("Extensions loaded: %d of %d", len(_extensions), len(BRIDGE_EXTENSIONS))

    # Post startup message to audit log
    if audit_channel:
        active = [n for n, c in [("events", event_poller), ("HUD", hud_updater), ("Lumen", lumen_poller)] if c]
        if _extensions:
            active.append(f"{len(_extensions)} extensions")
        embed = discord.Embed(
            title="Bridge Online",
            description=f"Systems active: {', '.join(active) or 'none'}",
            colour=discord.Colour.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=str(bot.user))
        try:
            await audit_channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.warning("Failed to post startup message: %s", exc)

    _initialized = True
    log.info("All systems ready — events, HUD, Lumen, commands active")


@bot.event
async def on_close():
    """Graceful shutdown: stop all background tasks and close the cache."""
    log.info("Shutting down bridge...")

    # Post shutdown message while connection is still alive
    if audit_channel is not None:
        embed = discord.Embed(
            title="Bridge Offline",
            description="Shutting down gracefully.",
            colour=discord.Colour.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        try:
            await audit_channel.send(embed=embed)
        except Exception:
            pass  # Best-effort — connection may already be closing

    # Stop extensions first (they may depend on core pollers)
    for ext in _extensions:
        try:
            await ext.stop()
        except Exception as exc:
            log.warning("Error stopping extension: %s", exc)

    # Stop all background pollers
    for name, component in [
        ("event_poller", event_poller),
        ("hud_updater", hud_updater),
        ("lumen_poller", lumen_poller),
    ]:
        if component is not None:
            try:
                await component.stop()
                log.info("Stopped %s", name)
            except Exception as exc:
                log.warning("Error stopping %s: %s", name, exc)

    # Close the SQLite cache
    if cache is not None:
        try:
            await cache.__aexit__(None, None, None)
            log.info("Cache closed")
        except Exception as exc:
            log.warning("Error closing cache: %s", exc)

    # Close persistent HTTP clients
    await gov_client.close()
    await anima_client.close()

    log.info("Shutdown complete")


def main():
    if not DISCORD_TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN environment variable is required")
    # Fail fast rather than silently connecting to no guild (issue #8)
    if not GUILD_ID:
        raise ValueError(
            "DISCORD_GUILD_ID environment variable is required and must not be 0"
        )
    setup_commands(bot, gov_client, anima_client)
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
