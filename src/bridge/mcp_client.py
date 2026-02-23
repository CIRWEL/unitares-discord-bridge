"""HTTP clients for governance-mcp and anima-mcp services."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class GovernanceClient:
    """Client for the governance-mcp HTTP API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.consecutive_failures = 0

    async def fetch_events(self, since: int = 0, limit: int = 50) -> list[dict]:
        """GET /api/events?since=N&limit=N. Returns [] on error."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}/api/events",
                    params={"since": since, "limit": limit},
                )
                resp.raise_for_status()
                self.consecutive_failures = 0
                return resp.json()
        except Exception as exc:
            self.consecutive_failures += 1
            log.warning("governance fetch_events failed (%d): %s", self.consecutive_failures, exc)
            return []

    async def fetch_health(self) -> dict | None:
        """GET /health. Returns None on error."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/health")
                resp.raise_for_status()
                self.consecutive_failures = 0
                return resp.json()
        except Exception as exc:
            self.consecutive_failures += 1
            log.warning("governance fetch_health failed (%d): %s", self.consecutive_failures, exc)
            return None

    async def call_tool(self, tool_name: str, arguments: dict) -> dict | None:
        """POST /v1/tools/call with {"name": ..., "arguments": ...}. Returns None on error."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/tools/call",
                    json={"name": tool_name, "arguments": arguments},
                )
                resp.raise_for_status()
                self.consecutive_failures = 0
                return resp.json()
        except Exception as exc:
            self.consecutive_failures += 1
            log.warning("governance call_tool(%s) failed (%d): %s", tool_name, self.consecutive_failures, exc)
            return None


class AnimaClient:
    """Client for the anima-mcp HTTP API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.is_online = True

    async def fetch_state(self) -> dict | None:
        """GET /state. Returns None on error. Sets is_online."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/state")
                resp.raise_for_status()
                self.is_online = True
                return resp.json()
        except Exception as exc:
            self.is_online = False
            log.warning("anima fetch_state failed: %s", exc)
            return None

    async def fetch_gallery(self, limit: int = 5) -> dict | None:
        """GET /gallery?limit=N. Returns None on error."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}/gallery",
                    params={"limit": limit},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.warning("anima fetch_gallery failed: %s", exc)
            return None

    async def fetch_drawing_image(self, filename: str) -> bytes | None:
        """GET /gallery/{filename}. Returns raw bytes on success, None on error."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.base_url}/gallery/{filename}")
                resp.raise_for_status()
                return resp.content
        except Exception as exc:
            log.warning("anima fetch_drawing_image(%s) failed: %s", filename, exc)
            return None
