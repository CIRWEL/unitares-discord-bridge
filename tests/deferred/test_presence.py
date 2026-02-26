from bridge.deferred.presence import verdict_to_role_name


def test_verdict_proceed():
    assert verdict_to_role_name("proceed") == "agent-active"


def test_verdict_guide():
    assert verdict_to_role_name("guide") == "agent-boundary"


def test_verdict_pause():
    assert verdict_to_role_name("pause") == "agent-degraded"


def test_verdict_reject():
    assert verdict_to_role_name("reject") == "agent-degraded"


def test_verdict_unknown():
    assert verdict_to_role_name("something_else") == "agent-active"
