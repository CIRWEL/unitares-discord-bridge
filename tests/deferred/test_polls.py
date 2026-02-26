"""Tests for the governance poll manager."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bridge.deferred.polls import (
    CONSERVATIVE_INDEX,
    PAUSE_OPTIONS,
    REJECT_OPTIONS,
    PollManager,
    _parse_tool_result,
    build_audit_embed,
    build_poll_embed,
    tally_votes,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_event(verdict="pause", agent_name="test-agent", agent_id="abc-123", eisv=None):
    """Helper to build a verdict_change event dict."""
    event = {
        "event_id": 42,
        "type": "verdict_change",
        "severity": "warning",
        "message": "Drift exceeded threshold",
        "agent_name": agent_name,
        "agent_id": agent_id,
        "from": "proceed",
        "to": verdict,
    }
    if eisv:
        event["eisv"] = eisv
    return event


# ---------------------------------------------------------------------------
# build_poll_embed
# ---------------------------------------------------------------------------


def test_build_poll_embed_pause():
    event = _make_event("pause", eisv={"E": 0.8, "I": 0.5, "S": 0.9, "V": 0.3})
    embed = build_poll_embed(event, "pause")

    assert isinstance(embed, discord.Embed)
    assert "Paused" in embed.title
    assert "test-agent" in embed.title
    assert embed.colour == discord.Colour.orange()

    # Check vote field contains all pause options
    vote_field = next(f for f in embed.fields if f.name == "Vote")
    assert "Resume" in vote_field.value
    assert "Hold" in vote_field.value
    assert "Dialectic" in vote_field.value

    # Check EISV field is present
    eisv_field = next(f for f in embed.fields if f.name == "EISV")
    assert "E=0.80" in eisv_field.value


def test_build_poll_embed_reject():
    event = _make_event("reject")
    embed = build_poll_embed(event, "reject")

    assert "Rejected" in embed.title
    assert embed.colour == discord.Colour.red()

    vote_field = next(f for f in embed.fields if f.name == "Vote")
    assert "Override" in vote_field.value
    assert "Uphold" in vote_field.value
    assert "Investigate" in vote_field.value


def test_build_poll_embed_no_eisv():
    event = _make_event("pause")
    embed = build_poll_embed(event, "pause")

    # No EISV field when not provided
    field_names = [f.name for f in embed.fields]
    assert "EISV" not in field_names


def test_build_poll_embed_footer():
    event = _make_event("pause")
    embed = build_poll_embed(event, "pause")
    assert "15 minutes" in embed.footer.text
    assert "conservative" in embed.footer.text.lower()


# ---------------------------------------------------------------------------
# build_audit_embed
# ---------------------------------------------------------------------------


def test_build_audit_embed_resumed():
    result = {
        "agent_id": "abc-123",
        "verdict_type": "pause",
        "winner": "Resume",
        "vote_counts": {"Resume": 3, "Hold": 1, "Dialectic": 0},
        "action_taken": "resumed",
    }
    embed = build_audit_embed(result)

    assert "Poll Resolved" in embed.title
    assert "pause" in embed.description
    assert "Resume" in embed.description
    assert embed.colour == discord.Colour.green()

    votes_field = next(f for f in embed.fields if f.name == "Votes")
    assert "Resume: 3" in votes_field.value


def test_build_audit_embed_held():
    result = {
        "agent_id": "def-456",
        "verdict_type": "reject",
        "winner": "Uphold",
        "vote_counts": {"Override": 0, "Uphold": 2, "Investigate": 1},
        "action_taken": "upheld",
    }
    embed = build_audit_embed(result)

    assert embed.colour == discord.Colour.light_grey()
    assert "Uphold" in embed.description


# ---------------------------------------------------------------------------
# tally_votes
# ---------------------------------------------------------------------------


def test_tally_resume_wins():
    counts = {"\u2705": 5, "\u23f8\ufe0f": 2, "\U0001f504": 1}
    winner, vote_counts = tally_votes(counts, PAUSE_OPTIONS)
    assert winner == "Resume"
    assert vote_counts == {"Resume": 5, "Hold": 2, "Dialectic": 1}


def test_tally_hold_wins():
    counts = {"\u2705": 1, "\u23f8\ufe0f": 3, "\U0001f504": 0}
    winner, vote_counts = tally_votes(counts, PAUSE_OPTIONS)
    assert winner == "Hold"
    assert vote_counts["Hold"] == 3


def test_tally_tie_goes_conservative_pause():
    """Tie between Resume and Hold -> conservative (Hold)."""
    counts = {"\u2705": 2, "\u23f8\ufe0f": 2, "\U0001f504": 0}
    winner, _ = tally_votes(counts, PAUSE_OPTIONS)
    assert winner == "Hold"


def test_tally_tie_goes_conservative_reject():
    """Tie between Override and Uphold -> conservative (Uphold)."""
    counts = {"\u2705": 3, "\u26d4": 3, "\U0001f50d": 0}
    winner, _ = tally_votes(counts, REJECT_OPTIONS)
    assert winner == "Uphold"


def test_tally_zero_votes_conservative():
    """No votes at all -> conservative option."""
    winner, vote_counts = tally_votes({}, PAUSE_OPTIONS)
    assert winner == "Hold"
    assert all(v == 0 for v in vote_counts.values())


def test_tally_three_way_tie():
    """Three-way tie -> conservative."""
    counts = {"\u2705": 1, "\u23f8\ufe0f": 1, "\U0001f504": 1}
    winner, _ = tally_votes(counts, PAUSE_OPTIONS)
    assert winner == "Hold"


def test_tally_override_wins():
    counts = {"\u2705": 4, "\u26d4": 1, "\U0001f50d": 2}
    winner, vote_counts = tally_votes(counts, REJECT_OPTIONS)
    assert winner == "Override"
    assert vote_counts["Override"] == 4


def test_tally_investigate_wins():
    counts = {"\u2705": 0, "\u26d4": 1, "\U0001f50d": 5}
    winner, _ = tally_votes(counts, REJECT_OPTIONS)
    assert winner == "Investigate"


# ---------------------------------------------------------------------------
# Cache poll methods
# ---------------------------------------------------------------------------


@pytest.fixture
def cache(tmp_path):
    from bridge.deferred.cache import DeferredCache
    return DeferredCache(str(tmp_path / "test.db"))


@pytest.mark.asyncio
async def test_save_and_get_active_polls(cache):
    async with cache:
        await cache.save_poll("p1", "agent-a", "pause", 111, 222, "2099-01-01T00:00:00+00:00")
        polls = await cache.get_active_polls()
        assert len(polls) == 1
        assert polls[0]["poll_id"] == "p1"
        assert polls[0]["agent_id"] == "agent-a"
        assert polls[0]["verdict_type"] == "pause"


@pytest.mark.asyncio
async def test_resolve_poll_removes_from_active(cache):
    async with cache:
        await cache.save_poll("p1", "agent-a", "pause", 111, 222, "2099-01-01T00:00:00+00:00")
        await cache.resolve_poll("p1")
        polls = await cache.get_active_polls()
        assert len(polls) == 0


@pytest.mark.asyncio
async def test_multiple_polls_only_active_returned(cache):
    async with cache:
        await cache.save_poll("p1", "a1", "pause", 111, 222, "2099-01-01T00:00:00+00:00")
        await cache.save_poll("p2", "a2", "reject", 333, 444, "2099-01-01T00:00:00+00:00")
        await cache.resolve_poll("p1")
        polls = await cache.get_active_polls()
        assert len(polls) == 1
        assert polls[0]["poll_id"] == "p2"


# ---------------------------------------------------------------------------
# PollManager._resolve_poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_poll_resume_calls_operator(cache):
    """When Resume wins, operator_resume_agent is called."""
    async with cache:
        gov = MagicMock()
        gov.call_tool = AsyncMock(return_value={"result": "ok"})

        pm = PollManager(gov, cache)
        pm.bot = None  # No bot -> empty reaction counts -> tie -> conservative

        # Save a poll
        await cache.save_poll("p1", "agent-x", "pause", 111, 222, "2020-01-01T00:00:00+00:00")

        # Override _fetch_reaction_counts to simulate Resume winning
        pm._fetch_reaction_counts = AsyncMock(
            return_value={"\u2705": 5, "\u23f8\ufe0f": 1, "\U0001f504": 0}
        )

        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        assert result["winner"] == "Resume"
        assert result["action_taken"] == "resumed"
        gov.call_tool.assert_called_once_with(
            "operator_resume_agent", {"agent_id": "agent-x"},
        )

        # Poll should be marked resolved
        assert len(await cache.get_active_polls()) == 0


@pytest.mark.asyncio
async def test_resolve_poll_hold_with_human_votes(cache):
    """When Hold wins with human votes, no governance action is taken."""
    async with cache:
        gov = MagicMock()
        gov.call_tool = AsyncMock()

        pm = PollManager(gov, cache)
        pm._fetch_reaction_counts = AsyncMock(
            return_value={"\u23f8\ufe0f": 3, "\u2705": 1, "\U0001f504": 0}
        )

        await cache.save_poll("p1", "agent-x", "pause", 111, 222, "2020-01-01T00:00:00+00:00")
        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        assert result["winner"] == "Hold"
        assert result["action_taken"] == "held"
        gov.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_poll_override_calls_operator(cache):
    """When Override wins on a reject poll, operator_resume_agent is called."""
    async with cache:
        gov = MagicMock()
        gov.call_tool = AsyncMock(return_value={"result": "ok"})

        pm = PollManager(gov, cache)
        pm._fetch_reaction_counts = AsyncMock(
            return_value={"\u2705": 3, "\u26d4": 1, "\U0001f50d": 0}
        )

        await cache.save_poll("p1", "agent-x", "reject", 111, 222, "2020-01-01T00:00:00+00:00")
        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        assert result["winner"] == "Override"
        assert result["action_taken"] == "resumed"
        gov.call_tool.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_poll_uphold_no_call(cache):
    """When Uphold wins on a reject poll, no resume call."""
    async with cache:
        gov = MagicMock()
        gov.call_tool = AsyncMock()

        pm = PollManager(gov, cache)
        pm._fetch_reaction_counts = AsyncMock(
            return_value={"\u2705": 0, "\u26d4": 2, "\U0001f50d": 1}
        )

        await cache.save_poll("p1", "agent-x", "reject", 111, 222, "2020-01-01T00:00:00+00:00")
        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        assert result["winner"] == "Uphold"
        assert result["action_taken"] == "upheld"
        gov.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_poll_dialectic_action(cache):
    """When Dialectic wins, request_dialectic_review is called."""
    async with cache:
        gov = MagicMock()
        gov.call_tool = AsyncMock(return_value={"result": "ok"})

        pm = PollManager(gov, cache)
        pm._fetch_reaction_counts = AsyncMock(
            return_value={"\u2705": 0, "\u23f8\ufe0f": 1, "\U0001f504": 3}
        )

        await cache.save_poll("p1", "agent-x", "pause", 111, 222, "2020-01-01T00:00:00+00:00")
        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        assert result["winner"] == "Dialectic"
        assert result["action_taken"] == "dialectic_requested"
        gov.call_tool.assert_called_once_with(
            "request_dialectic_review",
            {"agent_id": "agent-x", "reason": "Autonomous poll resolution: pause with no human votes"},
        )


@pytest.mark.asyncio
async def test_resolve_poll_resume_failed(cache):
    """When operator_resume_agent returns None, action is resume_failed."""
    async with cache:
        gov = MagicMock()
        gov.call_tool = AsyncMock(return_value=None)

        pm = PollManager(gov, cache)
        pm._fetch_reaction_counts = AsyncMock(
            return_value={"\u2705": 5, "\u23f8\ufe0f": 0, "\U0001f504": 0}
        )

        await cache.save_poll("p1", "agent-x", "pause", 111, 222, "2020-01-01T00:00:00+00:00")
        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        assert result["action_taken"] == "resume_failed"


# ---------------------------------------------------------------------------
# Expiry logic
# ---------------------------------------------------------------------------


def _mcp_wrap(data: dict) -> dict:
    """Wrap data in the MCP tool result envelope."""
    return {"result": {"content": [{"text": json.dumps(data)}]}}


@pytest.mark.asyncio
async def test_check_expired_polls_resolves_expired(cache):
    """Polls past their expires_at get resolved."""
    async with cache:
        gov = MagicMock()
        # Autonomous decision will call get_governance_metrics, then maybe
        # request_dialectic_review if degraded
        gov.call_tool = AsyncMock(side_effect=[
            _mcp_wrap({"eisv": {"E": 0.3, "I": 0.2, "S": 1.5, "V": 0.1}}),
            {"result": "ok"},  # dialectic request
        ])

        pm = PollManager(gov, cache)
        pm._fetch_reaction_counts = AsyncMock(return_value={})  # Zero votes -> autonomous
        pm.audit_channel = MagicMock()
        pm.audit_channel.send = AsyncMock()

        # Save a poll that expired an hour ago
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        await cache.save_poll("p1", "agent-x", "pause", 111, 222, past)

        await pm._check_expired_polls()

        # Should be resolved
        assert len(await cache.get_active_polls()) == 0
        # Audit embed should have been sent
        pm.audit_channel.send.assert_called_once()


@pytest.mark.asyncio
async def test_check_expired_polls_skips_future(cache):
    """Polls not yet expired are left alone."""
    async with cache:
        gov = MagicMock()
        pm = PollManager(gov, cache)

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        await cache.save_poll("p1", "agent-x", "pause", 111, 222, future)

        await pm._check_expired_polls()

        # Still active
        assert len(await cache.get_active_polls()) == 1


@pytest.mark.asyncio
async def test_check_expired_polls_bad_date(cache):
    """Polls with unparseable expires_at get resolved immediately."""
    async with cache:
        gov = MagicMock()
        pm = PollManager(gov, cache)

        await cache.save_poll("p1", "agent-x", "pause", 111, 222, "not-a-date")

        await pm._check_expired_polls()

        # Should be resolved due to bad date
        assert len(await cache.get_active_polls()) == 0


# ---------------------------------------------------------------------------
# Autonomous resolution (zero votes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autonomous_resolve_recovered_agent_resumes(cache):
    """Zero votes + recovered EISV -> auto-resume."""
    async with cache:
        gov = MagicMock()
        # First call: get_governance_metrics (from _autonomous_decision)
        # Second call: operator_resume_agent (from _resolve_poll action)
        gov.call_tool = AsyncMock(side_effect=[
            _mcp_wrap({"eisv": {"E": 0.8, "I": 0.7, "S": 0.5, "V": 0.6}}),
            {"result": "ok"},
        ])

        pm = PollManager(gov, cache)
        pm._fetch_reaction_counts = AsyncMock(return_value={})  # Zero human votes

        await cache.save_poll("p1", "agent-x", "pause", 111, 222, "2020-01-01T00:00:00+00:00")
        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        assert result["winner"] == "Resume"
        assert result["action_taken"] == "resumed"
        # Second call should be operator_resume_agent
        assert gov.call_tool.call_args_list[1][0] == (
            "operator_resume_agent", {"agent_id": "agent-x"},
        )


@pytest.mark.asyncio
async def test_autonomous_resolve_degraded_agent_dialectic(cache):
    """Zero votes + degraded EISV -> auto-request dialectic."""
    async with cache:
        gov = MagicMock()
        # First call: get_governance_metrics (degraded)
        # Second call: request_dialectic_review (from _resolve_poll action)
        gov.call_tool = AsyncMock(side_effect=[
            _mcp_wrap({"eisv": {"E": 0.2, "I": 0.3, "S": 1.5, "V": 0.1}}),
            {"result": "ok"},
        ])

        pm = PollManager(gov, cache)
        pm._fetch_reaction_counts = AsyncMock(return_value={})  # Zero votes

        await cache.save_poll("p1", "agent-x", "pause", 111, 222, "2020-01-01T00:00:00+00:00")
        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        assert result["winner"] == "Dialectic"
        assert result["action_taken"] == "dialectic_requested"
        assert gov.call_tool.call_args_list[1][0][0] == "request_dialectic_review"


@pytest.mark.asyncio
async def test_autonomous_resolve_reject_recovered_overrides(cache):
    """Zero votes on reject poll + recovered EISV -> Override."""
    async with cache:
        gov = MagicMock()
        gov.call_tool = AsyncMock(side_effect=[
            _mcp_wrap({"eisv": {"E": 0.9, "I": 0.8, "S": 0.3, "V": 0.7}}),
            {"result": "ok"},
        ])

        pm = PollManager(gov, cache)
        pm._fetch_reaction_counts = AsyncMock(return_value={})

        await cache.save_poll("p1", "agent-x", "reject", 111, 222, "2020-01-01T00:00:00+00:00")
        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        assert result["winner"] == "Override"
        assert result["action_taken"] == "resumed"


@pytest.mark.asyncio
async def test_autonomous_resolve_reject_degraded_investigates(cache):
    """Zero votes on reject poll + degraded EISV -> Investigate."""
    async with cache:
        gov = MagicMock()
        gov.call_tool = AsyncMock(side_effect=[
            _mcp_wrap({"eisv": {"E": 0.1, "I": 0.1, "S": 2.0, "V": 0.0}}),
            {"result": "ok"},
        ])

        pm = PollManager(gov, cache)
        pm._fetch_reaction_counts = AsyncMock(return_value={})

        await cache.save_poll("p1", "agent-x", "reject", 111, 222, "2020-01-01T00:00:00+00:00")
        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        assert result["winner"] == "Investigate"
        assert result["action_taken"] == "dialectic_requested"


@pytest.mark.asyncio
async def test_autonomous_resolve_metrics_failure_defaults_to_dialectic(cache):
    """When EISV fetch fails during autonomous decision, fallback to dialectic."""
    async with cache:
        gov = MagicMock()
        gov.call_tool = AsyncMock(side_effect=[
            None,  # get_governance_metrics fails
            {"result": "ok"},  # request_dialectic_review succeeds
        ])

        pm = PollManager(gov, cache)
        pm._fetch_reaction_counts = AsyncMock(return_value={})

        await cache.save_poll("p1", "agent-x", "pause", 111, 222, "2020-01-01T00:00:00+00:00")
        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        # With empty EISV, E=0, I=0, S=999 -> not recovered -> dialectic
        assert result["winner"] == "Dialectic"
        assert result["action_taken"] == "dialectic_requested"


@pytest.mark.asyncio
async def test_human_votes_override_autonomous(cache):
    """When humans vote, their decision wins even if autonomous would differ."""
    async with cache:
        gov = MagicMock()
        gov.call_tool = AsyncMock()  # No calls expected (Hold doesn't call)

        pm = PollManager(gov, cache)
        # Humans voted for Hold
        pm._fetch_reaction_counts = AsyncMock(
            return_value={"\u2705": 1, "\u23f8\ufe0f": 5, "\U0001f504": 0}
        )

        await cache.save_poll("p1", "agent-x", "pause", 111, 222, "2020-01-01T00:00:00+00:00")
        poll = (await cache.get_active_polls())[0]
        result = await pm._resolve_poll(poll)

        assert result["winner"] == "Hold"
        assert result["action_taken"] == "held"
        # No governance API calls made
        gov.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# _parse_tool_result
# ---------------------------------------------------------------------------


def test_parse_tool_result_unwraps():
    result = _mcp_wrap({"eisv": {"E": 0.8}})
    parsed = _parse_tool_result(result)
    assert parsed == {"eisv": {"E": 0.8}}


def test_parse_tool_result_none():
    assert _parse_tool_result(None) == {}


def test_parse_tool_result_empty():
    assert _parse_tool_result({"result": {}}) == {}
