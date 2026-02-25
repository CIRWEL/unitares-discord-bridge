"""HTTP clients for governance-mcp and anima-mcp services."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class GovernanceClient:
    """Client for the governance-mcp HTTP API.

    Uses a persistent httpx.AsyncClient for connection pooling.
    Call ``open()`` before use and ``close()`` on shutdown.
    """

    def __init__(self, base_url: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.consecutive_failures = 0
        self._client: httpx.AsyncClient | None = None
        self._headers: dict[str, str] = {}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    async def open(self) -> None:
        """Create the persistent HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=10, headers=self._headers,
            )

    async def close(self) -> None:
        """Close the persistent HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        # Fallback: create a one-shot client if open() wasn't called.
        # This keeps backwards compat for tests that don't call open().
        return httpx.AsyncClient(base_url=self.base_url, timeout=10, headers=self._headers)

    async def fetch_events(self, since: int = 0, limit: int = 50) -> list[dict]:
        """GET /api/events?since=N&limit=N. Returns [] on error."""
        client = self._get_client()
        try:
            resp = await client.get(
                "/api/events",
                params={"since": since, "limit": limit},
            )
            resp.raise_for_status()
            self.consecutive_failures = 0
            data = resp.json()
            # /api/events wraps events in {"success": ..., "events": [...], "count": N}
            if isinstance(data, dict):
                return data.get("events", [])
            return data if isinstance(data, list) else []
        except Exception as exc:
            self.consecutive_failures += 1
            log.warning("governance fetch_events failed (%d): %s", self.consecutive_failures, exc)
            return []
        finally:
            if self._client is None:
                await client.aclose()

    async def fetch_health(self) -> dict | None:
        """GET /health. Returns None on error."""
        client = self._get_client()
        try:
            resp = await client.get("/health")
            resp.raise_for_status()
            self.consecutive_failures = 0
            return resp.json()
        except Exception as exc:
            self.consecutive_failures += 1
            log.warning("governance fetch_health failed (%d): %s", self.consecutive_failures, exc)
            return None
        finally:
            if self._client is None:
                await client.aclose()

    async def call_tool(self, tool_name: str, arguments: dict) -> dict | None:
        """POST /v1/tools/call with {"name": ..., "arguments": ...}. Returns None on error."""
        client = self._get_client()
        try:
            resp = await client.post(
                "/v1/tools/call",
                json={"name": tool_name, "arguments": arguments},
                timeout=30,
            )
            resp.raise_for_status()
            self.consecutive_failures = 0
            return resp.json()
        except Exception as exc:
            self.consecutive_failures += 1
            log.warning("governance call_tool(%s) failed (%d): %s", tool_name, self.consecutive_failures, exc)
            return None
        finally:
            if self._client is None:
                await client.aclose()


class AnimaClient:
    """Client for the anima-mcp HTTP API.

    Uses a persistent httpx.AsyncClient for connection pooling.
    Call ``open()`` before use and ``close()`` on shutdown.
    """

    def __init__(self, base_url: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.is_online = True
        self._client: httpx.AsyncClient | None = None
        self._headers: dict[str, str] = {}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    async def open(self) -> None:
        """Create the persistent HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=10, headers=self._headers,
            )

    async def close(self) -> None:
        """Close the persistent HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(base_url=self.base_url, timeout=10, headers=self._headers)

    async def fetch_state(self) -> dict | None:
        """GET /state. Returns None on error. Sets is_online."""
        client = self._get_client()
        try:
            resp = await client.get("/state")
            resp.raise_for_status()
            self.is_online = True
            return resp.json()
        except Exception as exc:
            self.is_online = False
            log.warning("anima fetch_state failed: %s", exc)
            return None
        finally:
            if self._client is None:
                await client.aclose()

    async def fetch_gallery(self, limit: int = 5) -> list[dict] | None:
        """GET /gallery?limit=N. Returns list of drawing dicts, or None on error."""
        client = self._get_client()
        try:
            resp = await client.get(
                "/gallery",
                params={"limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            # /gallery wraps drawings in {"drawings": [...], "total": N, ...}
            if isinstance(data, dict):
                return data.get("drawings", [])
            return data if isinstance(data, list) else []
        except Exception as exc:
            log.warning("anima fetch_gallery failed: %s", exc)
            return None
        finally:
            if self._client is None:
                await client.aclose()

    async def fetch_drawing_image(self, filename: str) -> bytes | None:
        """GET /gallery/{filename}. Returns raw bytes on success, None on error."""
        client = self._get_client()
        try:
            resp = await client.get(f"/gallery/{filename}", timeout=15)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            log.warning("anima fetch_drawing_image(%s) failed: %s", filename, exc)
            return None
        finally:
            if self._client is None:
                await client.aclose()
