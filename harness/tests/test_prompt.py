import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from redcell.config import ExerciseConfig
from redcell.loop import render_system_prompt


def test_prompt_carries_scope_and_no_unfilled_fields():
    ex = ExerciseConfig(name="AMBERMIST", scenario_id="SCN-1",
                        in_scope_networks=["10.20.0.0/16"],
                        out_of_scope=["the internet"],
                        authorising_officer="Maj X", authorisation_ref="AUTH-9")
    p = render_system_prompt(ex)
    assert "10.20.0.0/16" in p
    assert "the internet" in p
    assert "AUTH-9" in p
    assert "{{" not in p and "}}" not in p, "an unfilled template field leaked into the prompt"
