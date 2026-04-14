import pytest
from bridge.cache import BridgeCache


@pytest.fixture
def cache(tmp_path):
    db_path = str(tmp_path / "test.db")
    return BridgeCache(db_path)


@pytest.mark.asyncio
async def test_event_cursor_default_zero(cache):
    async with cache:
        cursor = await cache.get_event_cursor()
        assert cursor == 0


@pytest.mark.asyncio
async def test_event_cursor_set_and_get(cache):
    async with cache:
        await cache.set_event_cursor(42)
        assert await cache.get_event_cursor() == 42


@pytest.mark.asyncio
async def test_hud_message(cache):
    async with cache:
        await cache.set_hud_message(111, 222)
        channel_id, message_id = await cache.get_hud_message()
        assert channel_id == 111
        assert message_id == 222
