import discord
from bridge.deferred.dialectic import (
    build_dialectic_post_embed, build_thesis_embed,
    build_antithesis_embed, build_synthesis_embed, build_resolution_embed,
)


def test_dialectic_post_embed():
    session = {"session_type": "recovery", "reason": "Risk threshold exceeded",
               "paused_agent_id": "abc12345-1234", "reviewer_agent_id": "def67890-5678",
               "phase": "thesis", "session_id": "a1b2c3d4e5f6g7h8"}
    embed = build_dialectic_post_embed(session)
    assert isinstance(embed, discord.Embed)
    assert "recovery" in embed.title.lower()


def test_dialectic_post_embed_defaults():
    embed = build_dialectic_post_embed({})
    assert isinstance(embed, discord.Embed)
    assert "?" in str(embed.fields[0].value)


def test_thesis_embed():
    msg = {"reasoning": "High complexity caused the issue",
           "root_cause": "Complexity spike", "proposed_conditions": ["Reduce to 0.3"]}
    embed = build_thesis_embed(msg)
    assert "Thesis" in embed.title
    assert "Complexity spike" in str(embed.fields)


def test_thesis_embed_minimal():
    msg = {"reasoning": "Something happened"}
    embed = build_thesis_embed(msg)
    assert embed.description == "Something happened"
    assert len(embed.fields) == 0


def test_antithesis_embed():
    msg = {"reasoning": "Metrics are concerning", "concerns": ["High risk", "Low coherence"],
           "observed_metrics": {"risk_score": 0.75, "coherence": 0.35}}
    embed = build_antithesis_embed(msg)
    assert "Antithesis" in embed.title
    assert embed.colour == discord.Colour.orange()


def test_antithesis_embed_minimal():
    msg = {"reasoning": "Not convinced"}
    embed = build_antithesis_embed(msg)
    assert embed.description == "Not convinced"
    assert len(embed.fields) == 0


def test_synthesis_agreed():
    msg = {"reasoning": "We agree", "agrees": True, "proposed_conditions": ["Monitor 24h"]}
    embed = build_synthesis_embed(msg, round_num=2)
    assert "Round 2" in embed.title
    assert embed.colour == discord.Colour.green()


def test_synthesis_not_agreed():
    msg = {"reasoning": "I disagree", "agrees": False}
    embed = build_synthesis_embed(msg, round_num=1)
    assert embed.colour != discord.Colour.green()


def test_resolution_resume():
    res = {"action": "resume", "reasoning": "Both agreed", "root_cause": "Complexity spike",
           "conditions": ["Reduce complexity", "Monitor"]}
    embed = build_resolution_embed(res)
    assert "RESUME" in embed.title
    assert embed.colour == discord.Colour.green()


def test_resolution_block():
    res = {"action": "block", "reasoning": "Cannot agree", "root_cause": "Unknown"}
    embed = build_resolution_embed(res)
    assert "BLOCK" in embed.title
    assert embed.colour == discord.Colour.red()


def test_resolution_no_conditions():
    res = {"action": "resume", "reasoning": "Clean resume", "root_cause": "Transient"}
    embed = build_resolution_embed(res)
    # Should still work without conditions
    assert isinstance(embed, discord.Embed)
