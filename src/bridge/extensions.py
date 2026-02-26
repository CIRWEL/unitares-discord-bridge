"""Extension loader for deferred bridge modules.

Enable extensions via the BRIDGE_EXTENSIONS environment variable:

    BRIDGE_EXTENSIONS=autonomy,polls

Each extension module in bridge.deferred must expose:

    async def setup(ctx: ExtensionContext) -> Extension

where Extension has:
    async def start() -> None
    async def stop() -> None
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

import discord

from bridge.cache import BridgeCache
from bridge.mcp_client import GovernanceClient, AnimaClient

log = logging.getLogger(__name__)


@dataclass
class ExtensionContext:
    """Everything an extension needs to wire itself up."""
    guild: discord.Guild
    channels: dict[str, discord.abc.GuildChannel]
    gov_client: GovernanceClient
    anima_client: AnimaClient
    cache: BridgeCache
    bot: discord.ext.commands.Bot


class Extension:
    """Protocol that extensions implement."""

    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError


async def load_extensions(
    names: list[str],
    ctx: ExtensionContext,
) -> list[Extension]:
    """Import and set up each named extension from bridge.deferred.

    Returns the list of successfully loaded extensions (skips failures).
    """
    loaded: list[Extension] = []
    for name in names:
        module_path = f"bridge.deferred.{name}"
        try:
            mod = importlib.import_module(module_path)
        except ImportError as exc:
            log.error("Extension %r not found: %s", name, exc)
            continue

        setup_fn = getattr(mod, "setup", None)
        if setup_fn is None:
            log.error("Extension %r has no setup() function", name)
            continue

        try:
            ext = await setup_fn(ctx)
            await ext.start()
            loaded.append(ext)
            log.info("Extension loaded: %s", name)
        except Exception as exc:
            log.error("Extension %r failed to start: %s", name, exc)

    return loaded
