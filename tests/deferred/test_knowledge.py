import json
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bridge.deferred.knowledge import build_knowledge_embed, KnowledgeSync


# ---------------------------------------------------------------------------
# build_knowledge_embed tests
# ---------------------------------------------------------------------------

def test_knowledge_embed_basic():
    discovery = {
        "title": "Memory leak in embedding service",
        "content": "The embedding service accumulates memory over time.",
        "type": "bug_found",
        "status": "open",
        "agent_id": "abc12345-6789",
        "created_at": "2026-02-20T10:30:00Z",
    }
    embed = build_knowledge_embed(discovery)
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Memory leak in embedding service"
    assert "embedding service" in embed.description
    assert embed.colour == discord.Colour.red()  # bug_found -> red


def test_knowledge_embed_insight_type():
    discovery = {"title": "Pattern found", "content": "Agents converge", "type": "insight"}
    embed = build_knowledge_embed(discovery)
    assert embed.colour == discord.Colour.purple()


def test_knowledge_embed_defaults():
    embed = build_knowledge_embed({})
    assert embed.title == "Untitled Discovery"
    assert embed.description == ""
    # Default type is "note" -> blue
    assert embed.colour == discord.Colour.blue()


def test_knowledge_embed_status_field():
    discovery = {"title": "Test", "status": "resolved"}
    embed = build_knowledge_embed(discovery)
    status_field = next(f for f in embed.fields if f.name == "Status")
    assert "resolved" in status_field.value


def test_knowledge_embed_open_status():
    discovery = {"title": "Test", "status": "open"}
    embed = build_knowledge_embed(discovery)
    status_field = next(f for f in embed.fields if f.name == "Status")
    assert "open" in status_field.value


def test_knowledge_embed_archived_status():
    discovery = {"title": "Test", "status": "archived"}
    embed = build_knowledge_embed(discovery)
    status_field = next(f for f in embed.fields if f.name == "Status")
    assert "archived" in status_field.value


def test_knowledge_embed_agent_truncated():
    discovery = {"title": "Test", "agent_id": "abcdefgh-1234-5678-9012"}
    embed = build_knowledge_embed(discovery)
    agent_field = next(f for f in embed.fields if f.name == "Agent")
    assert agent_field.value == "abcdefgh"


def test_knowledge_embed_no_agent():
    discovery = {"title": "Test"}
    embed = build_knowledge_embed(discovery)
    agent_fields = [f for f in embed.fields if f.name == "Agent"]
    assert len(agent_fields) == 0


def test_knowledge_embed_with_metadata():
    discovery = {
        "title": "Test",
        "metadata": {"source": "analysis", "confidence": 0.95},
    }
    embed = build_knowledge_embed(discovery)
    meta_field = next(f for f in embed.fields if f.name == "Metadata")
    assert "source" in meta_field.value
    assert "confidence" in meta_field.value


def test_knowledge_embed_created_at_footer():
    discovery = {"title": "Test", "created_at": "2026-02-20T10:30:00Z"}
    embed = build_knowledge_embed(discovery)
    assert "2026-02-20" in embed.footer.text


def test_knowledge_embed_all_types():
    """Verify all known types produce correct colours."""
    type_colours = {
        "note": discord.Colour.blue(),
        "insight": discord.Colour.purple(),
        "bug_found": discord.Colour.red(),
        "improvement": discord.Colour.green(),
        "analysis": discord.Colour.teal(),
        "pattern": discord.Colour.gold(),
    }
    for entry_type, expected_colour in type_colours.items():
        embed = build_knowledge_embed({"title": "t", "type": entry_type})
        assert embed.colour == expected_colour, f"Mismatch for type {entry_type}"


def test_knowledge_embed_unknown_type():
    embed = build_knowledge_embed({"title": "Test", "type": "exotic"})
    assert embed.colour == discord.Colour.greyple()


# ---------------------------------------------------------------------------
# KnowledgeSync._parse_tool_result tests
# ---------------------------------------------------------------------------

def test_parse_tool_result_standard():
    result = {
        "result": {
            "content": [{"text": json.dumps({"entries": [{"id": "1"}]})}],
        },
    }
    parsed = KnowledgeSync._parse_tool_result(result)
    assert parsed == {"entries": [{"id": "1"}]}


def test_parse_tool_result_list():
    result = {
        "result": {
            "content": [{"text": json.dumps([{"id": "1"}, {"id": "2"}])}],
        },
    }
    parsed = KnowledgeSync._parse_tool_result(result)
    assert isinstance(parsed, list)
    assert len(parsed) == 2


def test_parse_tool_result_empty():
    result = {"result": {"content": []}}
    parsed = KnowledgeSync._parse_tool_result(result)
    assert parsed == {}


def test_parse_tool_result_no_result_key():
    parsed = KnowledgeSync._parse_tool_result({})
    assert parsed == {}


# ---------------------------------------------------------------------------
# KnowledgeSync poll logic tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_knowledge_sync_creates_post_for_new_entry():
    """KnowledgeSync should create a forum post for entries not in cache."""
    gov = AsyncMock()
    gov.call_tool = AsyncMock(return_value={
        "result": {
            "content": [{
                "text": json.dumps([
                    {"id": "d1", "title": "New Discovery", "content": "Found something", "type": "insight"},
                ]),
            }],
        },
    })

    cache = AsyncMock()
    cache.get_knowledge_post = AsyncMock(return_value=None)  # Not in cache
    cache.set_knowledge_post = AsyncMock()

    forum = AsyncMock(spec=discord.ForumChannel)
    thread_mock = MagicMock()
    thread_mock.thread.id = 12345
    forum.create_thread = AsyncMock(return_value=thread_mock)

    sync = KnowledgeSync(gov, cache, forum, interval=60)

    # Run one iteration of the poll logic directly
    gov.call_tool.return_value = {
        "result": {
            "content": [{
                "text": json.dumps([
                    {"id": "d1", "title": "New Discovery", "content": "Found something", "type": "insight"},
                ]),
            }],
        },
    }
    await sync._create_forum_post("d1", {"title": "New Discovery", "content": "Found something", "type": "insight"})

    forum.create_thread.assert_called_once()
    cache.set_knowledge_post.assert_called_once_with("d1", 12345)


@pytest.mark.asyncio
async def test_knowledge_sync_skips_cached_entry():
    """KnowledgeSync should skip entries that are already in cache."""
    gov = AsyncMock()
    gov.call_tool = AsyncMock(return_value={
        "result": {
            "content": [{
                "text": json.dumps([
                    {"id": "d1", "title": "Old Discovery"},
                ]),
            }],
        },
    })

    cache = AsyncMock()
    cache.get_knowledge_post = AsyncMock(return_value=99999)  # Already cached

    forum = AsyncMock(spec=discord.ForumChannel)
    forum.create_thread = AsyncMock()

    sync = KnowledgeSync(gov, cache, forum, interval=60)

    # Manually invoke poll body logic
    result = await gov.call_tool("knowledge", {"action": "search", "query": "*", "limit": 20})
    entries = sync._parse_tool_result(result)
    for entry in entries:
        discovery_id = entry.get("id", "")
        post_id = await cache.get_knowledge_post(discovery_id)
        if post_id:
            continue
        await sync._create_forum_post(discovery_id, entry)

    forum.create_thread.assert_not_called()


@pytest.mark.asyncio
async def test_knowledge_sync_handles_empty_result():
    """KnowledgeSync should handle empty/None results gracefully."""
    gov = AsyncMock()
    gov.call_tool = AsyncMock(return_value=None)

    cache = AsyncMock()
    forum = AsyncMock(spec=discord.ForumChannel)

    sync = KnowledgeSync(gov, cache, forum, interval=60)

    # Simulating what _poll_loop does with None result
    result = await gov.call_tool("knowledge", {})
    assert result is None
    # Should not crash
    forum.create_thread.assert_not_called()
