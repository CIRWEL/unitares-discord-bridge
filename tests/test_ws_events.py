"""Tests for bridge.ws_events pure helpers.

The network-facing :class:`WSEventSubscriber` is not exercised here — its
value is hard to mock meaningfully without also mocking Discord. These
tests pin the classification logic so future event types don't silently
regress to "invisible in Discord".
"""

import discord

from bridge.ws_events import (
    broadcaster_event_to_embed,
    is_critical_broadcaster_event,
    ws_url_from_http,
)


# ---------------------------------------------------------------------------
# ws_url_from_http
# ---------------------------------------------------------------------------


def test_ws_url_from_http_plaintext():
    assert ws_url_from_http("http://localhost:8767") == "ws://localhost:8767/ws/eisv"


def test_ws_url_from_http_tls():
    assert (
        ws_url_from_http("https://gov.cirwel.org")
        == "wss://gov.cirwel.org/ws/eisv"
    )


def test_ws_url_from_http_strips_trailing_slash():
    assert (
        ws_url_from_http("http://localhost:8767/")
        == "ws://localhost:8767/ws/eisv"
    )


def test_ws_url_from_http_passes_through_unknown_scheme():
    # Not an obvious http(s) URL — caller's responsibility; best-effort append.
    assert ws_url_from_http("unix:///tmp/sock") == "unix:///tmp/sock/ws/eisv"


# ---------------------------------------------------------------------------
# broadcaster_event_to_embed — eisv_update and empty are dropped
# ---------------------------------------------------------------------------


def test_eisv_update_returns_none():
    assert broadcaster_event_to_embed({"type": "eisv_update", "coherence": 0.5}) is None


def test_missing_type_returns_none():
    assert broadcaster_event_to_embed({}) is None
    assert broadcaster_event_to_embed({"type": ""}) is None


# ---------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------


def test_lifecycle_paused_red():
    embed = broadcaster_event_to_embed({
        "type": "lifecycle_paused",
        "agent_label": "vigil",
        "reason": "silent for 30min",
    })
    assert embed is not None
    assert "paused" in embed.title.lower()
    assert embed.colour == discord.Colour.red()
    assert "silent for 30min" in embed.description


def test_lifecycle_resumed_green():
    embed = broadcaster_event_to_embed({
        "type": "lifecycle_resumed",
        "agent_label": "sentinel",
    })
    assert embed.colour == discord.Colour.green()


def test_lifecycle_stuck_red():
    embed = broadcaster_event_to_embed({
        "type": "lifecycle_stuck_detected",
        "agent_label": "watcher",
    })
    assert embed.colour == discord.Colour.red()


def test_lifecycle_loop_orange():
    embed = broadcaster_event_to_embed({
        "type": "lifecycle_loop_detected",
        "agent_label": "vigil",
    })
    assert embed.colour == discord.Colour.orange()


def test_lifecycle_created_blurple():
    # "created" is neutral — deliberately not using red/orange/green.
    embed = broadcaster_event_to_embed({
        "type": "lifecycle_created",
        "agent_label": "new-agent",
    })
    assert embed.colour == discord.Colour.blurple()


# ---------------------------------------------------------------------------
# Identity events
# ---------------------------------------------------------------------------


def test_identity_drift_orange():
    embed = broadcaster_event_to_embed({
        "type": "identity_drift",
        "agent_label": "vigil",
        "detail": "session fingerprint mismatch",
    })
    assert embed.colour == discord.Colour.orange()
    assert "drift" in embed.title.lower()
    assert "fingerprint mismatch" in embed.description


def test_identity_assurance_change_blue():
    embed = broadcaster_event_to_embed({
        "type": "identity_assurance_change",
        "agent_label": "sentinel",
    })
    # No explicit warning colour for routine assurance changes.
    assert embed.colour == discord.Colour.blue()


# ---------------------------------------------------------------------------
# Knowledge events
# ---------------------------------------------------------------------------


def test_knowledge_write_carries_discovery_type_and_summary():
    embed = broadcaster_event_to_embed({
        "type": "knowledge_write",
        "agent_label": "sentinel",
        "discovery_type": "finding",
        "summary": "coordinated coherence drop across 3 agents",
        "tags": ["sentinel", "coordinated_coherence_drop", "high"],
    })
    assert "finding" in embed.title
    assert "coordinated coherence drop" in embed.description
    # high-severity tag → orange
    assert embed.colour == discord.Colour.orange()


def test_knowledge_write_critical_tag_red():
    embed = broadcaster_event_to_embed({
        "type": "knowledge_write",
        "tags": ["critical"],
    })
    assert embed.colour == discord.Colour.red()


def test_knowledge_write_truncates_long_summary():
    embed = broadcaster_event_to_embed({
        "type": "knowledge_write",
        "summary": "A" * 500,
    })
    assert embed.description.endswith("...")
    assert len(embed.description) <= 210


def test_knowledge_confidence_clamped_orange():
    embed = broadcaster_event_to_embed({
        "type": "knowledge_confidence_clamped",
        "agent_label": "opus",
        "summary": "overconfident claim",
    })
    assert embed.colour == discord.Colour.orange()


# ---------------------------------------------------------------------------
# Circuit breaker events
# ---------------------------------------------------------------------------


def test_circuit_breaker_trip_red():
    embed = broadcaster_event_to_embed({
        "type": "circuit_breaker_trip",
        "reason": "pool exhausted",
    })
    assert embed.colour == discord.Colour.red()
    assert "tripped" in embed.title.lower()
    assert "pool exhausted" in embed.description


def test_circuit_breaker_reset_green():
    embed = broadcaster_event_to_embed({"type": "circuit_breaker_reset"})
    assert embed.colour == discord.Colour.green()


# ---------------------------------------------------------------------------
# Unknown event types fall through to a generic renderer, not None
# ---------------------------------------------------------------------------


def test_unknown_future_type_renders_generically():
    # If someone adds a new broadcaster event class, we want it visible
    # immediately — not silently dropped.
    embed = broadcaster_event_to_embed({
        "type": "new_future_event_class",
        "agent_label": "x",
    })
    assert embed is not None
    assert "new future event class" in embed.title.lower()


# ---------------------------------------------------------------------------
# Agent field resolution
# ---------------------------------------------------------------------------


def test_agent_field_prefers_label_then_name_then_id():
    e1 = broadcaster_event_to_embed({
        "type": "lifecycle_paused",
        "agent_label": "opus",
        "agent_name": "gpt",
        "agent_id": "abc-def",
    })
    assert any(f.value == "opus" for f in e1.fields)

    e2 = broadcaster_event_to_embed({
        "type": "lifecycle_paused",
        "agent_name": "gpt",
        "agent_id": "abc-def",
    })
    assert any(f.value == "gpt" for f in e2.fields)

    e3 = broadcaster_event_to_embed({
        "type": "lifecycle_paused",
        "agent_id": "abcdef0123456789",
    })
    # Truncated agent id (first 12 chars)
    assert any(f.value == "abcdef012345" for f in e3.fields)

    e4 = broadcaster_event_to_embed({"type": "lifecycle_paused"})
    assert any(f.value == "system" for f in e4.fields)


# ---------------------------------------------------------------------------
# is_critical_broadcaster_event
# ---------------------------------------------------------------------------


def test_critical_trip():
    assert is_critical_broadcaster_event({"type": "circuit_breaker_trip"})


def test_critical_lifecycle_paused():
    assert is_critical_broadcaster_event({"type": "lifecycle_paused"})


def test_critical_lifecycle_silent_critical():
    assert is_critical_broadcaster_event({"type": "lifecycle_silent_critical"})


def test_critical_lifecycle_stuck():
    assert is_critical_broadcaster_event({"type": "lifecycle_stuck_detected"})


def test_critical_tag_elevates():
    assert is_critical_broadcaster_event({
        "type": "knowledge_write",
        "tags": ["critical"],
    })


def test_non_critical_by_default():
    assert not is_critical_broadcaster_event({"type": "knowledge_write"})
    assert not is_critical_broadcaster_event({"type": "lifecycle_resumed"})
    assert not is_critical_broadcaster_event({"type": "identity_drift"})
