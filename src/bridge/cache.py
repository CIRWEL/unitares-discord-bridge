"""SQLite state cache for event cursors, channel mappings, and HUD state."""

from __future__ import annotations

import json

import aiosqlite


class BridgeCache:
    """Async context manager wrapping an aiosqlite database for bridge state."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> "BridgeCache":
        self._db = await aiosqlite.connect(self._db_path)
        await self._create_tables()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _create_tables(self) -> None:
        assert self._db is not None
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_channels (
                agent_id   TEXT PRIMARY KEY,
                channel_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS dialectic_posts (
                dialectic_id TEXT PRIMARY KEY,
                post_id      INTEGER
            );
            CREATE TABLE IF NOT EXISTS knowledge_posts (
                discovery_id TEXT PRIMARY KEY,
                post_id      INTEGER
            );
            """
        )

    # -- event cursor --------------------------------------------------------

    async def get_event_cursor(self) -> int:
        """Return the last-seen event cursor, defaulting to 0."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT value FROM kv WHERE key = 'event_cursor'"
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def set_event_cursor(self, cursor: int) -> None:
        """Upsert the event cursor."""
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO kv (key, value) VALUES ('event_cursor', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(cursor),),
        )
        await self._db.commit()

    # -- agent channels ------------------------------------------------------

    async def get_agent_channel(self, agent_id: str) -> int | None:
        """Look up the Discord channel ID for an agent, or None."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT channel_id FROM agent_channels WHERE agent_id = ?",
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else None

    async def set_agent_channel(self, agent_id: str, channel_id: int) -> None:
        """Upsert the channel mapping for an agent."""
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO agent_channels (agent_id, channel_id) VALUES (?, ?)"
            " ON CONFLICT(agent_id) DO UPDATE SET channel_id = excluded.channel_id",
            (agent_id, channel_id),
        )
        await self._db.commit()

    # -- HUD message ---------------------------------------------------------

    async def get_hud_message(self) -> tuple[int, int] | None:
        """Return (channel_id, message_id) for the HUD message, or None."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT value FROM kv WHERE key = 'hud_message'"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        return (data["channel_id"], data["message_id"])

    async def set_hud_message(self, channel_id: int, message_id: int) -> None:
        """Store the HUD message location."""
        assert self._db is not None
        value = json.dumps({"channel_id": channel_id, "message_id": message_id})
        await self._db.execute(
            "INSERT INTO kv (key, value) VALUES ('hud_message', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (value,),
        )
        await self._db.commit()

    # -- dialectic posts -----------------------------------------------------

    async def get_dialectic_post(self, dialectic_id: str) -> int | None:
        """Look up the Discord message ID for a dialectic thread."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT post_id FROM dialectic_posts WHERE dialectic_id = ?",
            (dialectic_id,),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else None

    async def set_dialectic_post(self, dialectic_id: str, post_id: int) -> None:
        """Upsert the post mapping for a dialectic."""
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO dialectic_posts (dialectic_id, post_id) VALUES (?, ?)"
            " ON CONFLICT(dialectic_id) DO UPDATE SET post_id = excluded.post_id",
            (dialectic_id, post_id),
        )
        await self._db.commit()

    # -- knowledge posts -----------------------------------------------------

    async def get_knowledge_post(self, discovery_id: str) -> int | None:
        """Look up the Discord message ID for a knowledge discovery."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT post_id FROM knowledge_posts WHERE discovery_id = ?",
            (discovery_id,),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else None

    async def set_knowledge_post(self, discovery_id: str, post_id: int) -> None:
        """Upsert the post mapping for a knowledge discovery."""
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO knowledge_posts (discovery_id, post_id) VALUES (?, ?)"
            " ON CONFLICT(discovery_id) DO UPDATE SET post_id = excluded.post_id",
            (discovery_id, post_id),
        )
        await self._db.commit()
