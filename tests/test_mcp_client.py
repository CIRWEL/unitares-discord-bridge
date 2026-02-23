import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bridge.mcp_client import GovernanceClient, AnimaClient


# --- helpers ---

_SENTINEL = object()


def make_mock_response(status_code=200, json_data=_SENTINEL, content=b""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {} if json_data is _SENTINEL else json_data
    resp.content = content
    resp.raise_for_status = MagicMock()
    return resp


def mock_httpx_client(method="get", response=None):
    """Returns a patch context for httpx.AsyncClient."""
    mock_client_instance = AsyncMock()
    getattr(mock_client_instance, method).return_value = response
    mock_client = MagicMock()
    mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
    return patch("bridge.mcp_client.httpx.AsyncClient", mock_client)


# --- GovernanceClient tests ---

@pytest.mark.asyncio
async def test_governance_fetch_events():
    events = [{"id": 1, "type": "identity.created"}, {"id": 2, "type": "dialectic.started"}]
    resp = make_mock_response(json_data=events)

    with mock_httpx_client("get", resp):
        client = GovernanceClient("http://localhost:8767")
        result = await client.fetch_events(since=0, limit=50)

    assert result == events
    assert client.consecutive_failures == 0


@pytest.mark.asyncio
async def test_governance_fetch_events_server_down():
    mock_client_instance = AsyncMock()
    mock_client_instance.get.side_effect = Exception("Connection refused")
    mock_client = MagicMock()
    mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("bridge.mcp_client.httpx.AsyncClient", mock_client):
        client = GovernanceClient("http://localhost:8767")
        result = await client.fetch_events()

    assert result == []
    assert client.consecutive_failures == 1


@pytest.mark.asyncio
async def test_governance_consecutive_failures_reset():
    """After a failure, a successful call resets consecutive_failures to 0."""
    # First: simulate a failure
    mock_fail_instance = AsyncMock()
    mock_fail_instance.get.side_effect = Exception("timeout")
    mock_fail = MagicMock()
    mock_fail.return_value.__aenter__ = AsyncMock(return_value=mock_fail_instance)
    mock_fail.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("bridge.mcp_client.httpx.AsyncClient", mock_fail):
        client = GovernanceClient("http://localhost:8767")
        await client.fetch_events()

    assert client.consecutive_failures == 1

    # Second: simulate a success
    resp = make_mock_response(json_data=[])
    with mock_httpx_client("get", resp):
        result = await client.fetch_events()

    assert result == []
    assert client.consecutive_failures == 0


@pytest.mark.asyncio
async def test_governance_fetch_health():
    health = {"status": "ok", "uptime": 12345}
    resp = make_mock_response(json_data=health)

    with mock_httpx_client("get", resp):
        client = GovernanceClient("http://localhost:8767")
        result = await client.fetch_health()

    assert result == health


@pytest.mark.asyncio
async def test_governance_fetch_health_error():
    mock_client_instance = AsyncMock()
    mock_client_instance.get.side_effect = Exception("down")
    mock_client = MagicMock()
    mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("bridge.mcp_client.httpx.AsyncClient", mock_client):
        client = GovernanceClient("http://localhost:8767")
        result = await client.fetch_health()

    assert result is None
    assert client.consecutive_failures == 1


@pytest.mark.asyncio
async def test_governance_call_tool():
    tool_result = {"agent_id": "abc-123", "status": "created"}
    resp = make_mock_response(json_data=tool_result)

    with mock_httpx_client("post", resp):
        client = GovernanceClient("http://localhost:8767")
        result = await client.call_tool("onboard", {"name": "test-agent"})

    assert result == tool_result
    assert client.consecutive_failures == 0


@pytest.mark.asyncio
async def test_governance_call_tool_error():
    mock_client_instance = AsyncMock()
    mock_client_instance.post.side_effect = Exception("500 error")
    mock_client = MagicMock()
    mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("bridge.mcp_client.httpx.AsyncClient", mock_client):
        client = GovernanceClient("http://localhost:8767")
        result = await client.call_tool("onboard", {})

    assert result is None
    assert client.consecutive_failures == 1


# --- AnimaClient tests ---

@pytest.mark.asyncio
async def test_anima_fetch_state():
    state = {"warmth": 0.7, "clarity": 0.8, "stability": 0.9, "presence": 0.6}
    resp = make_mock_response(json_data=state)

    with mock_httpx_client("get", resp):
        client = AnimaClient("http://100.79.215.83:8766")
        result = await client.fetch_state()

    assert result == state
    assert client.is_online is True


@pytest.mark.asyncio
async def test_anima_fetch_state_offline():
    mock_client_instance = AsyncMock()
    mock_client_instance.get.side_effect = Exception("Connection refused")
    mock_client = MagicMock()
    mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("bridge.mcp_client.httpx.AsyncClient", mock_client):
        client = AnimaClient("http://100.79.215.83:8766")
        result = await client.fetch_state()

    assert result is None
    assert client.is_online is False


@pytest.mark.asyncio
async def test_anima_fetch_gallery():
    gallery = {"drawings": [{"filename": "art_001.png", "timestamp": 1708700000}]}
    resp = make_mock_response(json_data=gallery)

    with mock_httpx_client("get", resp):
        client = AnimaClient("http://100.79.215.83:8766")
        result = await client.fetch_gallery(limit=5)

    assert result == gallery


@pytest.mark.asyncio
async def test_anima_fetch_gallery_error():
    mock_client_instance = AsyncMock()
    mock_client_instance.get.side_effect = Exception("timeout")
    mock_client = MagicMock()
    mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("bridge.mcp_client.httpx.AsyncClient", mock_client):
        client = AnimaClient("http://100.79.215.83:8766")
        result = await client.fetch_gallery()

    assert result is None


@pytest.mark.asyncio
async def test_anima_fetch_drawing_image():
    image_bytes = b"\x89PNG\r\n\x1a\nfake_image_data"
    resp = make_mock_response(content=image_bytes)

    with mock_httpx_client("get", resp):
        client = AnimaClient("http://100.79.215.83:8766")
        result = await client.fetch_drawing_image("art_001.png")

    assert result == image_bytes


@pytest.mark.asyncio
async def test_anima_fetch_drawing_image_error():
    mock_client_instance = AsyncMock()
    mock_client_instance.get.side_effect = Exception("404")
    mock_client = MagicMock()
    mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("bridge.mcp_client.httpx.AsyncClient", mock_client):
        client = AnimaClient("http://100.79.215.83:8766")
        result = await client.fetch_drawing_image("nonexistent.png")

    assert result is None
