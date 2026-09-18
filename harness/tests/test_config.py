import pytest
from redcell.config import ExerciseConfig, Settings, EndpointConfig


def test_scope_must_not_be_empty():
    with pytest.raises(ValueError):
        ExerciseConfig(name="x", in_scope_networks=[])


def test_scope_ok():
    ex = ExerciseConfig(name="x", in_scope_networks=["10.0.0.0/8"])
    assert ex.attack_phases[0] == "reconnaissance"


def test_base_url_trailing_slash_stripped():
    assert EndpointConfig(base_url="http://x/v1/").base_url == "http://x/v1"


def test_team_lookup():
    s = Settings(exercise=ExerciseConfig(name="x", in_scope_networks=["10.0.0.0/8"]),
                 teams=[{"team_id": "blue-team-01"}])
    assert s.team("blue-team-01").team_id == "blue-team-01"
    with pytest.raises(KeyError):
        s.team("nope")
