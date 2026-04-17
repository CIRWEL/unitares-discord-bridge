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
async def test_event_cursor_corrupt_uuid_resets_to_zero(cache, caplog):
    # Simulates a past bug where governance emitted UUID event_ids and the
    # poller wrote one into the cursor. get_event_cursor must return 0 (not
    # raise) so the poll loop can recover.
    async with cache:
        await cache._db.execute(
            "INSERT INTO kv (key, value) VALUES ('event_cursor', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("fcd718be-0243-4a26-b503-79d4a3d7bfb1",),
        )
        await cache._db.commit()
        cursor = await cache.get_event_cursor()
        assert cursor == 0


@pytest.mark.asyncio
async def test_hud_message(cache):
    async with cache:
        await cache.set_hud_message(111, 222)
        channel_id, message_id = await cache.get_hud_message()
        assert channel_id == 111
        assert message_id == 222
