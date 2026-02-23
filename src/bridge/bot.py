import asyncio
import logging
import os

import discord
from discord.ext import commands

from bridge.config import (
    DISCORD_TOKEN, GUILD_ID, GOVERNANCE_URL, ANIMA_URL,
    EVENT_POLL_INTERVAL, HUD_UPDATE_INTERVAL, DB_PATH,
)
from bridge.cache import BridgeCache
from bridge.mcp_client import GovernanceClient, AnimaClient
from bridge.server_setup import ensure_server_structure
from bridge.event_poller import EventPoller
from bridge.hud import HUDUpdater

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


@bot.event
async def on_ready():
    global cache, event_poller, hud_updater

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

    # Start the event poller if both channels exist
    events_ch = channels.get("events")
    alerts_ch = channels.get("alerts")
    if events_ch and alerts_ch:
        event_poller = EventPoller(
            gov_client, cache, events_ch, alerts_ch, EVENT_POLL_INTERVAL,
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

    log.info("Phase 1 ready — events flowing, HUD updating")


def main():
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
