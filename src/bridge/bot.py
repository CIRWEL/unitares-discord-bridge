import asyncio
import logging
import os

import discord
from discord.ext import commands

from bridge.config import (
    DISCORD_TOKEN, GUILD_ID, GOVERNANCE_URL, ANIMA_URL,
    EVENT_POLL_INTERVAL, HUD_UPDATE_INTERVAL, SENSOR_POLL_INTERVAL, DB_PATH,
)
from bridge.cache import BridgeCache
from bridge.mcp_client import GovernanceClient, AnimaClient
from bridge.server_setup import ensure_server_structure
from bridge.event_poller import EventPoller
from bridge.hud import HUDUpdater
from bridge.presence import PresenceManager
from bridge.lumen import LumenPoller
from bridge.dialectic import DialecticSync
from bridge.knowledge import KnowledgeSync
from bridge.commands import setup_commands
from bridge.polls import PollManager
from bridge.resonance import ResonanceTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("bridge")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

gov_client = GovernanceClient(GOVERNANCE_URL)
anima_client = AnimaClient(ANIMA_URL)

cache: BridgeCache | None = None
event_poller: EventPoller | None = None
hud_updater: HUDUpdater | None = None
presence_manager: PresenceManager | None = None
lumen_poller: LumenPoller | None = None
dialectic_sync: DialecticSync | None = None
knowledge_sync: KnowledgeSync | None = None
poll_manager: PollManager | None = None
resonance_tracker: ResonanceTracker | None = None


@bot.event
async def on_ready():
    global cache, event_poller, hud_updater, presence_manager, lumen_poller, dialectic_sync, knowledge_sync, poll_manager, resonance_tracker

    log.info("Bridge online as %s", bot.user)
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        log.error("Guild %d not found", GUILD_ID)
        return

    channels = await ensure_server_structure(guild)
    log.info("Server structure ready: %d channels", len(channels))

    # Open the SQLite cache for event cursors, HUD state, etc.
    os.makedirs(os.path.dirname(DB_PATH) or "data", exist_ok=True)
    cache = BridgeCache(DB_PATH)
    await cache.__aenter__()

    # Set up presence manager if AGENTS category and lobby exist
    agents_category = {c.name: c for c in guild.categories}.get("AGENTS")
    lobby_ch = channels.get("agent-lobby")
    if agents_category and lobby_ch:
        presence_manager = PresenceManager(
            gov_client, cache, guild, agents_category, lobby_ch,
        )
        await presence_manager.start()
        log.info("Presence manager started")

    # Set up poll manager for governance votes
    audit_ch = channels.get("audit-log")
    poll_manager = PollManager(gov_client, cache, audit_channel=audit_ch)
    poll_manager.bot = bot
    if audit_ch:
        await poll_manager.start()
        log.info("Poll manager started")

    # Set up resonance tracker if #resonance channel exists
    resonance_ch = channels.get("resonance")
    if resonance_ch:
        resonance_tracker = ResonanceTracker(resonance_ch)
        log.info("Resonance tracker ready")

    # Start the event poller if both channels exist
    events_ch = channels.get("events")
    alerts_ch = channels.get("alerts")
    if events_ch and alerts_ch:
        event_poller = EventPoller(
            gov_client, cache, events_ch, alerts_ch, EVENT_POLL_INTERVAL,
            presence_manager=presence_manager,
            poll_manager=poll_manager,
            resonance_tracker=resonance_tracker,
            audit_channel=audit_ch,
            guild=guild,
        )
        await event_poller.start()
        log.info("Event poller started")

    # Start the HUD updater if the channel exists
    hud_ch = channels.get("governance-hud")
    if hud_ch:
        hud_updater = HUDUpdater(
            gov_client, cache, hud_ch, HUD_UPDATE_INTERVAL,
        )
        await hud_updater.start()
        log.info("HUD updater started")

    # Start the Lumen poller if all three channels exist
    stream_ch = channels.get("lumen-stream")
    art_ch = channels.get("lumen-art")
    sensor_ch = channels.get("lumen-sensors")
    if stream_ch and art_ch and sensor_ch:
        lumen_poller = LumenPoller(
            anima_client, stream_ch, art_ch, sensor_ch, SENSOR_POLL_INTERVAL,
        )
        await lumen_poller.start()
        log.info("Lumen poller started")

    # Start the dialectic sync if the forum channel exists
    forum_ch = channels.get("dialectic-forum")
    if forum_ch:
        dialectic_sync = DialecticSync(gov_client, cache, forum_ch)
        await dialectic_sync.start()
        log.info("Dialectic sync started")

    # Start the knowledge sync if the discoveries forum exists
    discoveries_ch = channels.get("discoveries")
    if discoveries_ch:
        knowledge_sync = KnowledgeSync(gov_client, cache, discoveries_ch)
        await knowledge_sync.start()
        log.info("Knowledge sync started")

    # Sync the slash command tree
    try:
        await bot.tree.sync()
        log.info("Slash command tree synced")
    except Exception as exc:
        log.error("Failed to sync command tree: %s", exc)

    log.info("All systems ready — events, HUD, presence, Lumen, dialectic, knowledge, commands active")


def main():
    setup_commands(bot, gov_client, anima_client)
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
