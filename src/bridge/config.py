import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))

GOVERNANCE_URL = os.environ.get("GOVERNANCE_MCP_URL", "http://localhost:8767")
ANIMA_URL = os.environ.get("ANIMA_MCP_URL", "")

GOVERNANCE_TOKEN = os.environ.get("GOVERNANCE_API_TOKEN", "")
ANIMA_TOKEN = os.environ.get("ANIMA_API_TOKEN", "")

EVENT_POLL_INTERVAL = int(os.environ.get("EVENT_POLL_INTERVAL", "10"))
HUD_UPDATE_INTERVAL = int(os.environ.get("HUD_UPDATE_INTERVAL", "30"))
SENSOR_POLL_INTERVAL = int(os.environ.get("SENSOR_POLL_INTERVAL", "300"))

DB_PATH = os.environ.get("BRIDGE_DB_PATH", "data/bridge.db")

# Per-class routing — when enabled, broadcaster events that map to a
# violation class (via /v1/taxonomy reverse-lookup) are mirrored to a
# class-specific text channel in addition to the main #events channel.
# Lets operators subscribe to specific violation classes and mute the rest.
# Disabled by default so existing deployments don't get new channels without
# opt-in.
CLASS_ROUTING_ENABLED = os.environ.get(
    "BRIDGE_CLASS_ROUTING_ENABLED", ""
).lower() in ("1", "true", "yes", "on")

# Operator-managed channel for lease-plane Phase B promotion-eligibility
# transitions (event_type=lease_plane_phase_b_transition emitted by Sentinel).
# Empty/0 disables routing — events still flow to the default activity/signals
# channels via the standard dispatch path. Channel must already exist; the
# bridge does NOT auto-create it.
LEASE_PLANE_PHASE_B_CHANNEL_ID = int(
    os.environ.get("DISCORD_LEASE_PLANE_PHASE_B_CHANNEL_ID", "0") or "0"
)
