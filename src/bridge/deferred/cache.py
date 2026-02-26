"""Extended cache with tables needed by deferred bridge modules."""

from __future__ import annotations

from bridge.cache import BridgeCache


class DeferredCache(BridgeCache):
    """BridgeCache extended with poll, dialectic, and knowledge post tables.

    Used by deferred modules (polls, dialectic, knowledge) that need
    additional state persistence beyond v1 core.
    """

    async def _create_tables(self) -> None:
        await super()._create_tables()
        assert self._db is not None
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS dialectic_posts (
                dialectic_id TEXT PRIMARY KEY,
                post_id      INTEGER
            );
            CREATE TABLE IF NOT EXISTS knowledge_posts (
                discovery_id TEXT PRIMARY KEY,
                post_id      INTEGER
            );
            CREATE TABLE IF NOT EXISTS poll_state (
                poll_id      TEXT PRIMARY KEY,
                agent_id     TEXT,
                verdict_type TEXT,
                message_id   INTEGER,
                channel_id   INTEGER,
                expires_at   TEXT,
                resolved     INTEGER DEFAULT 0
            );
            """
        )

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

    # -- poll state ----------------------------------------------------------

    async def save_poll(
        self,
        poll_id: str,
        agent_id: str,
        verdict_type: str,
        message_id: int,
        channel_id: int,
        expires_at: str,
    ) -> None:
        """Insert a new poll record."""
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO poll_state"
            " (poll_id, agent_id, verdict_type, message_id, channel_id, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (poll_id, agent_id, verdict_type, message_id, channel_id, expires_at),
        )
        await self._db.commit()

    async def get_active_polls(self) -> list[dict]:
        """Return all unresolved polls."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT poll_id, agent_id, verdict_type, message_id, channel_id, expires_at"
            " FROM poll_state WHERE resolved = 0"
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "poll_id": r[0],
                "agent_id": r[1],
                "verdict_type": r[2],
                "message_id": r[3],
                "channel_id": r[4],
                "expires_at": r[5],
            }
            for r in rows
        ]

    async def resolve_poll(self, poll_id: str) -> None:
        """Mark a poll as resolved."""
        assert self._db is not None
        await self._db.execute(
            "UPDATE poll_state SET resolved = 1 WHERE poll_id = ?",
            (poll_id,),
        )
        await self._db.commit()
