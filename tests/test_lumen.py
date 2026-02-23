import discord
from bridge.lumen import build_sensor_embed, build_drawing_embed


def test_sensor_embed():
    state = {
        "ambient_temp": 24.3, "humidity": 38.0, "pressure": 827.0,
        "light": 142.0, "cpu_temp": 62.0, "memory_percent": 41.0,
        "warmth": 0.7, "clarity": 0.6, "stability": 0.8, "presence": 0.5,
        "neural": {"delta": 0.8, "theta": 0.3, "alpha": 0.6, "beta": 0.5, "gamma": 0.4},
    }
    embed = build_sensor_embed(state)
    assert isinstance(embed, discord.Embed)
    assert "24.3" in embed.description
    assert "827" in embed.description
    assert "\u03b4=" in embed.description


def test_sensor_embed_missing_neural():
    state = {"ambient_temp": 20.0}
    embed = build_sensor_embed(state)
    assert isinstance(embed, discord.Embed)


def test_drawing_embed():
    drawing = {"filename": "lumen_drawing_20260223_140000.png", "era": "geometric", "manual": False}
    embed = build_drawing_embed(drawing)
    assert isinstance(embed, discord.Embed)
    assert "geometric" in embed.description.lower()


def test_drawing_embed_manual():
    drawing = {"filename": "test.png", "era": "pointillist", "manual": True}
    embed = build_drawing_embed(drawing)
    # Should indicate manual somehow
    found = False
    for field in embed.fields:
        if "yes" in str(field.value).lower():
            found = True
    assert found
