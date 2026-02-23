"""Tests for the autonomous governance engine."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bridge.autonomy import (
    AutonomyEngine,
    RECOVERY_E_MIN,
    RECOVERY_I_MIN,
    RECOVERY_S_MAX,
    RISK_WARNING_THRESHOLD,
    _is_recovered,
    _parse_tool_result,
    build_auto_resume_embed,
    build_auto_dialectic_embed,
    build_neighbor_warning_embed,
    build_audit_action_embed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mcp_wrap(data: dict) -> dict:
    """Wrap data in the MCP tool result envelope."""
    return {"result": {"content": [{"text": json.dumps(data)}]}}


def _make_gov_client(**overrides):
    """Create a mock GovernanceClient."""
    gov = MagicMock()
    gov.call_tool = AsyncMock(return_value=None)
    for k, v in overrides.items():
        setattr(gov, k, v)
    return gov


def _make_engine(gov=None, alerts=None, audit=None):
    """Create an AutonomyEngine with mock dependencies."""
    if gov is None:
        gov = _make_gov_client()
    cache = MagicMock()
    if alerts is None:
        alerts = MagicMock(spec=discord.TextChannel)
        alerts.send = AsyncMock()
    if audit is None:
        audit = MagicMock(spec=discord.TextChannel)
        audit.send = AsyncMock()
    return AutonomyEngine(gov, cache, alerts_channel=alerts, audit_channel=audit)


# ---------------------------------------------------------------------------
# Pure embed builder tests
# ---------------------------------------------------------------------------


class TestBuildAutoResumeEmbed:
    def test_basic_fields(self):
        metrics = {"E": 0.75, "I": 0.65, "S": 0.80, "V": 0.50}
        embed = build_auto_resume_embed("abc123456789", metrics)

        assert isinstance(embed, discord.Embed)
        assert "Auto-Resumed" in embed.title
        assert "abc123456789" in embed.description
        assert embed.colour == discord.Colour.green()

    def test_eisv_values_in_field(self):
        metrics = {"E": 0.75, "I": 0.65, "S": 0.80, "V": 0.50}
        embed = build_auto_resume_embed("abc123", metrics)

        eisv_field = next(f for f in embed.fields if f.name == "EISV at Resume")
        assert "E=0.75" in eisv_field.value
        assert "I=0.65" in eisv_field.value
        assert "S=0.80" in eisv_field.value
        assert "V=0.50" in eisv_field.value

    def test_nested_eisv(self):
        """Metrics wrapped in an eisv key should also work."""
        metrics = {"eisv": {"E": 0.9, "I": 0.8, "S": 0.3, "V": 0.7}}
        embed = build_auto_resume_embed("test-agent", metrics)
        eisv_field = next(f for f in embed.fields if f.name == "EISV at Resume")
        assert "E=0.90" in eisv_field.value

    def test_footer(self):
        embed = build_auto_resume_embed("x", {"E": 0, "I": 0, "S": 0, "V": 0})
        assert "Autonomous" in embed.footer.text

    def test_agent_id_truncated(self):
        long_id = "a" * 50
        embed = build_auto_resume_embed(long_id, {"E": 0, "I": 0, "S": 0, "V": 0})
        assert long_id[:12] in embed.description


class TestBuildAutoDialecticEmbed:
    def test_basic_fields(self):
        embed = build_auto_dialectic_embed("agent-xyz123", "Drift exceeded threshold")

        assert isinstance(embed, discord.Embed)
        assert "Auto-Dialectic" in embed.title
        assert "agent-xyz1" in embed.description
        assert embed.colour == discord.Colour.gold()

    def test_reason_in_field(self):
        embed = build_auto_dialectic_embed("agent-xyz", "Critical instability")
        reason_field = next(f for f in embed.fields if f.name == "Reason")
        assert "Critical instability" in reason_field.value

    def test_footer(self):
        embed = build_auto_dialectic_embed("x", "reason")
        assert "Autonomous" in embed.footer.text


class TestBuildNeighborWarningEmbed:
    def test_basic_fields(self):
        embed = build_neighbor_warning_embed("risky-agent-id", 0.85)

        assert isinstance(embed, discord.Embed)
        assert "Neighbor Risk" in embed.title
        assert "risky-agent" in embed.description
        assert embed.colour == discord.Colour.orange()

    def test_risk_value_formatted(self):
        embed = build_neighbor_warning_embed("agent-x", 0.75)
        risk_field = next(f for f in embed.fields if f.name == "Risk")
        assert "75%" in risk_field.value

    def test_advisory_field(self):
        embed = build_neighbor_warning_embed("agent-x", 0.9)
        advisory = next(f for f in embed.fields if f.name == "Advisory")
        assert "caution" in advisory.value.lower()

    def test_footer_informational(self):
        embed = build_neighbor_warning_embed("x", 0.8)
        assert "Informational" in embed.footer.text


class TestBuildAuditActionEmbed:
    def test_basic_fields(self):
        embed = build_audit_action_embed(
            "auto-resume", "agent-abc123456", "EISV recovered", "resumed",
        )

        assert isinstance(embed, discord.Embed)
        assert "auto-resume" in embed.title
        assert embed.colour == discord.Colour.blurple()

    def test_agent_field(self):
        embed = build_audit_action_embed("auto-dialectic", "agent-long-id-here", "reason", "ok")
        agent_field = next(f for f in embed.fields if f.name == "Agent")
        assert "agent-long-" in agent_field.value

    def test_result_field(self):
        embed = build_audit_action_embed("test", "agent", "reason", "success")
        result_field = next(f for f in embed.fields if f.name == "Result")
        assert "success" in result_field.value

    def test_reason_field(self):
        embed = build_audit_action_embed("test", "agent", "My detailed reason", "ok")
        reason_field = next(f for f in embed.fields if f.name == "Reason")
        assert "My detailed reason" in reason_field.value

    def test_has_timestamp(self):
        embed = build_audit_action_embed("test", "agent", "reason", "ok")
        assert embed.timestamp is not None


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestParseToolResult:
    def test_unwraps_mcp_envelope(self):
        result = _mcp_wrap({"eisv": {"E": 0.8}})
        parsed = _parse_tool_result(result)
        assert parsed == {"eisv": {"E": 0.8}}

    def test_returns_empty_dict_on_none(self):
        assert _parse_tool_result(None) == {}

    def test_returns_empty_dict_on_missing_content(self):
        assert _parse_tool_result({"result": {}}) == {}

    def test_returns_empty_dict_on_empty_content(self):
        assert _parse_tool_result({"result": {"content": []}}) == {}


class TestIsRecovered:
    def test_recovered(self):
        assert _is_recovered({"E": 0.7, "I": 0.6, "S": 0.5}) is True

    def test_e_too_low(self):
        assert _is_recovered({"E": 0.3, "I": 0.6, "S": 0.5}) is False

    def test_i_too_low(self):
        assert _is_recovered({"E": 0.7, "I": 0.3, "S": 0.5}) is False

    def test_s_too_high(self):
        assert _is_recovered({"E": 0.7, "I": 0.6, "S": 1.5}) is False

    def test_boundary_e(self):
        # E must be > 0.5, not >=
        assert _is_recovered({"E": 0.5, "I": 0.6, "S": 0.5}) is False

    def test_boundary_i(self):
        assert _is_recovered({"E": 0.6, "I": 0.5, "S": 0.5}) is False

    def test_boundary_s(self):
        # S must be < 1.0, not <=
        assert _is_recovered({"E": 0.6, "I": 0.6, "S": 1.0}) is False

    def test_just_over_boundary(self):
        assert _is_recovered({"E": 0.51, "I": 0.51, "S": 0.99}) is True

    def test_missing_keys_defaults_to_not_recovered(self):
        assert _is_recovered({}) is False


# ---------------------------------------------------------------------------
# AutonomyEngine.handle_verdict_event
# ---------------------------------------------------------------------------


class TestHandleVerdictEvent:
    @pytest.mark.asyncio
    async def test_calls_dialectic_review(self):
        gov = _make_gov_client()
        gov.call_tool = AsyncMock(return_value={"result": "ok"})
        engine = _make_engine(gov=gov)

        event = {
            "agent_id": "agent-abc",
            "to": "pause",
            "message": "Drift threshold exceeded",
        }
        await engine.handle_verdict_event(event)

        gov.call_tool.assert_called_once_with(
            "request_dialectic_review",
            {"agent_id": "agent-abc", "reason": "Drift threshold exceeded"},
        )

    @pytest.mark.asyncio
    async def test_posts_to_alerts(self):
        alerts = MagicMock(spec=discord.TextChannel)
        alerts.send = AsyncMock()
        engine = _make_engine(alerts=alerts)

        event = {"agent_id": "agent-abc", "to": "reject", "message": "Rejected"}
        await engine.handle_verdict_event(event)

        alerts.send.assert_called_once()
        embed = alerts.send.call_args.kwargs["embed"]
        assert "Auto-Dialectic" in embed.title

    @pytest.mark.asyncio
    async def test_posts_audit(self):
        audit = MagicMock(spec=discord.TextChannel)
        audit.send = AsyncMock()
        engine = _make_engine(audit=audit)

        event = {"agent_id": "agent-abc", "to": "pause", "message": "reason"}
        await engine.handle_verdict_event(event)

        audit.send.assert_called_once()
        embed = audit.send.call_args.kwargs["embed"]
        assert "auto-dialectic" in embed.title

    @pytest.mark.asyncio
    async def test_handles_tool_failure_gracefully(self):
        gov = _make_gov_client()
        gov.call_tool = AsyncMock(side_effect=Exception("Connection refused"))
        engine = _make_engine(gov=gov)

        event = {"agent_id": "agent-abc", "to": "pause", "message": "reason"}
        # Should not raise
        await engine.handle_verdict_event(event)

    @pytest.mark.asyncio
    async def test_default_reason_when_no_message(self):
        gov = _make_gov_client()
        gov.call_tool = AsyncMock(return_value={"result": "ok"})
        engine = _make_engine(gov=gov)

        event = {"agent_id": "agent-abc", "to": "pause"}
        await engine.handle_verdict_event(event)

        call_args = gov.call_tool.call_args[0]
        assert "pause" in call_args[1]["reason"]


# ---------------------------------------------------------------------------
# AutonomyEngine.handle_drift_event
# ---------------------------------------------------------------------------


class TestHandleDriftEvent:
    @pytest.mark.asyncio
    async def test_calls_dialectic_review(self):
        gov = _make_gov_client()
        gov.call_tool = AsyncMock(return_value={"result": "ok"})
        engine = _make_engine(gov=gov)

        event = {"agent_id": "agent-drift", "axis": "complexity", "value": 0.95}
        await engine.handle_drift_event(event)

        gov.call_tool.assert_called_once()
        call_args = gov.call_tool.call_args[0]
        assert call_args[0] == "request_dialectic_review"
        assert "complexity" in call_args[1]["reason"]
        assert "0.95" in call_args[1]["reason"]

    @pytest.mark.asyncio
    async def test_audit_logged(self):
        audit = MagicMock(spec=discord.TextChannel)
        audit.send = AsyncMock()
        engine = _make_engine(audit=audit)

        event = {"agent_id": "agent-drift", "axis": "risk", "value": 0.88}
        await engine.handle_drift_event(event)

        audit.send.assert_called_once()
        embed = audit.send.call_args.kwargs["embed"]
        assert "auto-dialectic-drift" in embed.title

    @pytest.mark.asyncio
    async def test_handles_tool_failure(self):
        gov = _make_gov_client()
        gov.call_tool = AsyncMock(side_effect=Exception("Timeout"))
        engine = _make_engine(gov=gov)

        event = {"agent_id": "agent-drift", "axis": "x", "value": 0.9}
        await engine.handle_drift_event(event)  # Should not raise


# ---------------------------------------------------------------------------
# AutonomyEngine.handle_risk_event
# ---------------------------------------------------------------------------


class TestHandleRiskEvent:
    @pytest.mark.asyncio
    async def test_warns_neighbor_channels(self):
        engine = _make_engine()

        # Set up a mock presence_manager with agent channels
        presence = MagicMock()
        ch1 = MagicMock(spec=discord.TextChannel)
        ch1.name = "agent-alice"
        ch1.topic = "Check-ins for alice (alice123...)"
        ch1.send = AsyncMock()

        ch2 = MagicMock(spec=discord.TextChannel)
        ch2.name = "agent-bob"
        ch2.topic = "Check-ins for bob (bob12345...)"
        ch2.send = AsyncMock()

        presence.agents_category = MagicMock()
        presence.agents_category.channels = [ch1, ch2]

        event = {"agent_id": "alice12345678", "value": 0.85}
        await engine.handle_risk_event(event, presence)

        # ch1 should be skipped (it's alice's own channel)
        ch1.send.assert_not_called()
        # ch2 should receive the warning
        ch2.send.assert_called_once()
        embed = ch2.send.call_args.kwargs["embed"]
        assert "Neighbor Risk" in embed.title

    @pytest.mark.asyncio
    async def test_skips_below_threshold(self):
        audit = MagicMock(spec=discord.TextChannel)
        audit.send = AsyncMock()
        engine = _make_engine(audit=audit)

        presence = MagicMock()
        presence.agents_category = MagicMock()
        presence.agents_category.channels = []

        event = {"agent_id": "agent-x", "value": 0.50}  # Below 70%
        await engine.handle_risk_event(event, presence)

        # No audit should be logged for sub-threshold risk
        audit.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_presence_manager(self):
        engine = _make_engine()

        event = {"agent_id": "agent-x", "value": 0.90}
        # Should handle None presence gracefully
        await engine.handle_risk_event(event, None)

    @pytest.mark.asyncio
    async def test_audit_logged_with_count(self):
        audit = MagicMock(spec=discord.TextChannel)
        audit.send = AsyncMock()
        engine = _make_engine(audit=audit)

        presence = MagicMock()
        ch = MagicMock(spec=discord.TextChannel)
        ch.name = "agent-other"
        ch.topic = "Check-ins for other (other123...)"
        ch.send = AsyncMock()
        presence.agents_category = MagicMock()
        presence.agents_category.channels = [ch]

        event = {"agent_id": "risky-agent", "value": 0.80}
        await engine.handle_risk_event(event, presence)

        audit.send.assert_called_once()
        embed = audit.send.call_args.kwargs["embed"]
        assert "neighbor-warning" in embed.title


# ---------------------------------------------------------------------------
# AutonomyEngine._check_paused_agents (auto-resume loop)
# ---------------------------------------------------------------------------


class TestAutoResumeLoop:
    @pytest.mark.asyncio
    async def test_resumes_recovered_agent(self):
        """Agent with recovered EISV gets auto-resumed."""
        gov = _make_gov_client()

        # First call: list_agents returns one paused agent
        # Second call: get_governance_metrics returns recovered EISV
        # Third call: operator_resume_agent succeeds
        gov.call_tool = AsyncMock(side_effect=[
            _mcp_wrap({"agents": [
                {"agent_id": "agent-recovered", "verdict": "pause"},
            ]}),
            _mcp_wrap({"eisv": {"E": 0.8, "I": 0.7, "S": 0.5, "V": 0.6}}),
            {"result": "ok"},
        ])

        alerts = MagicMock(spec=discord.TextChannel)
        alerts.send = AsyncMock()
        audit = MagicMock(spec=discord.TextChannel)
        audit.send = AsyncMock()

        engine = _make_engine(gov=gov, alerts=alerts, audit=audit)
        await engine._check_paused_agents()

        # Should have called: list_agents, get_governance_metrics, operator_resume_agent
        assert gov.call_tool.call_count == 3
        assert gov.call_tool.call_args_list[2][0] == (
            "operator_resume_agent", {"agent_id": "agent-recovered"},
        )

        # Should post to alerts
        alerts.send.assert_called_once()
        embed = alerts.send.call_args.kwargs["embed"]
        assert "Auto-Resumed" in embed.title

        # Should post to audit
        audit.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_degraded_agent(self):
        """Agent with still-degraded EISV is not resumed."""
        gov = _make_gov_client()

        gov.call_tool = AsyncMock(side_effect=[
            _mcp_wrap({"agents": [
                {"agent_id": "agent-degraded", "verdict": "pause"},
            ]}),
            _mcp_wrap({"eisv": {"E": 0.3, "I": 0.2, "S": 1.5, "V": 0.1}}),
        ])

        alerts = MagicMock(spec=discord.TextChannel)
        alerts.send = AsyncMock()

        engine = _make_engine(gov=gov, alerts=alerts)
        await engine._check_paused_agents()

        # Should only call list_agents + get_governance_metrics (no resume)
        assert gov.call_tool.call_count == 2
        alerts.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_non_paused_agents(self):
        """Agents with proceed or guide verdicts are ignored."""
        gov = _make_gov_client()

        gov.call_tool = AsyncMock(side_effect=[
            _mcp_wrap({"agents": [
                {"agent_id": "agent-active", "verdict": "proceed"},
                {"agent_id": "agent-guided", "verdict": "guide"},
            ]}),
        ])

        engine = _make_engine(gov=gov)
        await engine._check_paused_agents()

        # Only list_agents should be called
        assert gov.call_tool.call_count == 1

    @pytest.mark.asyncio
    async def test_handles_list_agents_returning_list(self):
        """list_agents may return a flat list instead of {agents: [...]}."""
        gov = _make_gov_client()

        gov.call_tool = AsyncMock(side_effect=[
            _mcp_wrap([
                {"agent_id": "agent-paused", "verdict": "pause"},
            ]),
            _mcp_wrap({"eisv": {"E": 0.9, "I": 0.9, "S": 0.1, "V": 0.9}}),
            {"result": "ok"},
        ])

        engine = _make_engine(gov=gov)
        await engine._check_paused_agents()

        # Should handle the list format and still resume
        assert gov.call_tool.call_count == 3

    @pytest.mark.asyncio
    async def test_handles_empty_agent_list(self):
        """Empty agent list should not crash."""
        gov = _make_gov_client()
        gov.call_tool = AsyncMock(return_value=_mcp_wrap({"agents": []}))

        engine = _make_engine(gov=gov)
        await engine._check_paused_agents()

        assert gov.call_tool.call_count == 1

    @pytest.mark.asyncio
    async def test_handles_metrics_fetch_failure(self):
        """If get_governance_metrics fails, skip agent gracefully."""
        gov = _make_gov_client()

        gov.call_tool = AsyncMock(side_effect=[
            _mcp_wrap({"agents": [
                {"agent_id": "agent-paused", "verdict": "pause"},
            ]}),
            None,  # metrics fetch fails
        ])

        engine = _make_engine(gov=gov)
        await engine._check_paused_agents()

        # Should not crash, and should not try to resume
        assert gov.call_tool.call_count == 2


# ---------------------------------------------------------------------------
# AutonomyEngine._audit
# ---------------------------------------------------------------------------


class TestAudit:
    @pytest.mark.asyncio
    async def test_posts_to_audit_channel(self):
        audit = MagicMock(spec=discord.TextChannel)
        audit.send = AsyncMock()
        engine = _make_engine(audit=audit)

        await engine._audit("test-action", "agent-x", "reason text", "success")

        audit.send.assert_called_once()
        embed = audit.send.call_args.kwargs["embed"]
        assert "test-action" in embed.title

    @pytest.mark.asyncio
    async def test_no_audit_channel_logs_only(self):
        engine = _make_engine(audit=None)
        engine.audit_channel = None

        # Should not raise
        await engine._audit("test-action", "agent-x", "reason", "result")

    @pytest.mark.asyncio
    async def test_handles_discord_send_failure(self):
        audit = MagicMock(spec=discord.TextChannel)
        audit.send = AsyncMock(side_effect=discord.HTTPException(
            MagicMock(), "Server error",
        ))
        engine = _make_engine(audit=audit)

        # Should not raise
        await engine._audit("test-action", "agent-x", "reason", "result")
