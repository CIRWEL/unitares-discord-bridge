import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))

GOVERNANCE_URL = os.environ.get("GOVERNANCE_MCP_URL", "http://localhost:8767")
# Default changed from a private Tailscale IP to localhost (issue #6).
# Set ANIMA_MCP_URL in .env (see .env.example) for the real endpoint.
ANIMA_URL = os.environ.get("ANIMA_MCP_URL", "http://localhost:8766")

GOVERNANCE_TOKEN = os.environ.get("GOVERNANCE_API_TOKEN", "")
ANIMA_TOKEN = os.environ.get("ANIMA_API_TOKEN", "")

EVENT_POLL_INTERVAL = int(os.environ.get("EVENT_POLL_INTERVAL", "10"))
HUD_UPDATE_INTERVAL = int(os.environ.get("HUD_UPDATE_INTERVAL", "30"))
SENSOR_POLL_INTERVAL = int(os.environ.get("SENSOR_POLL_INTERVAL", "300"))
# Drawing loop was previously hardcoded at 60 s (issue #11)
DRAWING_POLL_INTERVAL = int(os.environ.get("DRAWING_POLL_INTERVAL", "60"))

DB_PATH = os.environ.get("BRIDGE_DB_PATH", "data/bridge.db")

# Comma-separated list of deferred extensions to enable (e.g. "autonomy,polls")
BRIDGE_EXTENSIONS = [
    s.strip() for s in os.environ.get("BRIDGE_EXTENSIONS", "").split(",") if s.strip()
]
