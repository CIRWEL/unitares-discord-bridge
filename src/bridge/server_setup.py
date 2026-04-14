"""Auto-create Discord server structure (channels, categories, roles)."""

import logging

import discord

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Desired server structure
# ---------------------------------------------------------------------------

CHANNEL_STRUCTURE: dict[str, dict[str, dict[str, str]]] = {
    "GOVERNANCE": {
        "events": {"type": "text", "topic": "All governance events — verdicts, risk, drift"},
        "alerts": {"type": "text", "topic": "Critical only — pause, reject, risk > 70%"},
        "governance-hud": {"type": "text", "topic": "Auto-updating system status"},
    },
    "LUMEN": {
        "lumen-art": {"type": "text", "topic": "Lumen's drawings"},
        "lumen-sensors": {"type": "text", "topic": "Environmental sensor readings"},
    },
    "CONTROL": {
        "commands": {"type": "text", "topic": "Slash commands for governance actions"},
        "audit-log": {"type": "text", "topic": "All bot actions logged here"},
    },
}

ROLES: dict[str, discord.Colour] = {
    "Governance Admin": discord.Colour.dark_teal(),
    "observer": discord.Colour.light_grey(),
    "lumen": discord.Colour.blue(),
}


# ---------------------------------------------------------------------------
# Ensure everything exists
# ---------------------------------------------------------------------------

async def ensure_server_structure(
    guild: discord.Guild,
) -> dict[str, discord.abc.GuildChannel]:
    """Ensure all required roles, categories, and channels exist in *guild*.

    Returns a mapping of ``channel_name -> channel`` for every channel in the
    structure (whether it already existed or was freshly created).
    """

    # ---- Roles -------------------------------------------------------------
    existing_roles = {r.name: r for r in guild.roles}
    for role_name, colour in ROLES.items():
        if role_name not in existing_roles:
            await guild.create_role(name=role_name, colour=colour)
            log.info("Created role: %s", role_name)

    # ---- Categories & channels ---------------------------------------------
    existing_categories = {c.name: c for c in guild.categories}
    channel_map: dict[str, discord.abc.GuildChannel] = {}

    for category_name, channels in CHANNEL_STRUCTURE.items():
        # Ensure category exists
        category = existing_categories.get(category_name)
        if category is None:
            category = await guild.create_category(category_name)
            log.info("Created category: %s", category_name)

        # Index channels already present in this category
        existing_channels = {ch.name: ch for ch in category.channels}

        for ch_name, ch_cfg in channels.items():
            channel = existing_channels.get(ch_name)
            if channel is None:
                ch_type = ch_cfg["type"]
                topic = ch_cfg.get("topic", "")
                if ch_type == "forum":
                    channel = await guild.create_forum(
                        name=ch_name, category=category, topic=topic,
                    )
                    log.info("Created forum channel: %s/%s", category_name, ch_name)
                else:
                    channel = await guild.create_text_channel(
                        name=ch_name, category=category, topic=topic,
                    )
                    log.info("Created text channel: %s/%s", category_name, ch_name)

            channel_map[ch_name] = channel

    log.info(
        "Server structure verified — %d channels mapped", len(channel_map),
    )
    return channel_map
