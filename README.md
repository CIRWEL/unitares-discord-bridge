# UNITARES Discord Bridge

Discord bot that surfaces UNITARES governance events and Lumen state in Discord.

## What It Does

- **Governance events** — Check-ins, verdicts, dialectic sessions
- **Lumen state** — Sensor readings, creature status from the embodied agent
- **HUD updates** — Live status channels
- **Slash commands** — Status, health, resume, and Lumen snapshots

## Prerequisites

1. A running UNITARES governance MCP server
2. A running Anima/Lumen MCP server (optional, for sensor/creature data)
3. A Discord bot token and guild ID

## Installation

```bash
git clone https://github.com/CIRWEL/unitares-discord-bridge.git
cd unitares-discord-bridge
pip install -e ".[dev]"
```

## Configuration

Copy `.env.example` to `.env` and set:

| Variable | Description |
|----------|-------------|
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `DISCORD_GUILD_ID` | Discord server (guild) ID |
| `GOVERNANCE_MCP_URL` | Governance MCP server URL (default: `http://localhost:8767`) |
| `ANIMA_MCP_URL` | Anima/Lumen MCP URL (optional) |

Optional: `GOVERNANCE_API_TOKEN`, `ANIMA_API_TOKEN` for authenticated MCP calls.

## Run

```bash
unitares-bridge
# or
python -m bridge.bot
```

## License

MIT
