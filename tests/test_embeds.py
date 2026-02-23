import discord
from bridge.embeds import event_to_embed, is_critical_event


def test_verdict_change_embed():
    event = {"event_id": 5, "type": "verdict_change", "severity": "warning",
             "message": "Verdict changed", "agent_id": "abc", "agent_name": "opus",
             "timestamp": "2026-02-23T14:32:00Z", "from": "proceed", "to": "guide"}
    embed = event_to_embed(event)
    assert isinstance(embed, discord.Embed)
    assert "Verdict Change" in embed.title
    assert embed.colour == discord.Colour.orange()


def test_agent_new_embed():
    event = {"event_id": 1, "type": "agent_new", "severity": "info",
             "message": "New agent", "agent_id": "abc", "agent_name": "test",
             "timestamp": "2026-02-23T10:00:00Z"}
    embed = event_to_embed(event)
    assert embed.colour == discord.Colour.blue()


def test_critical_severity_is_red():
    event = {"event_id": 10, "type": "risk_threshold", "severity": "critical",
             "message": "Risk above 70%", "agent_id": "abc", "agent_name": "test",
             "timestamp": "2026-02-23T10:00:00Z", "threshold": 0.7, "direction": "up", "value": 0.75}
    embed = event_to_embed(event)
    assert embed.colour == discord.Colour.red()


def test_is_critical_for_pause():
    assert is_critical_event({"type": "verdict_change", "to": "pause", "severity": "warning"})


def test_is_not_critical_for_proceed():
    assert not is_critical_event({"type": "verdict_change", "to": "proceed", "severity": "info"})


def test_is_critical_for_critical_severity():
    assert is_critical_event({"type": "risk_threshold", "severity": "critical"})
