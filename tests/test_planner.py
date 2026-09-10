from pathlib import Path

import pytest

from cip.llm import Plan, ReplayPlanner, record_fixture
from cip.models import FactQuery


def test_record_replay_and_explicit_miss(tmp_path: Path):
    plan = Plan(action="count", query=FactQuery(), family_id=None, field=None, search_query=None)
    record_fixture(tmp_path, "How many?", plan)
    output, trace = ReplayPlanner(tmp_path).plan("How many?")
    assert output == plan
    assert "hand-authored" in trace["provenance"]
    with pytest.raises(FileNotFoundError):
        ReplayPlanner(tmp_path).plan("A changed request")


def test_model_plan_cannot_silently_ignore_a_family_restriction():
    with pytest.raises(ValueError, match="ignored_arguments"):
        Plan(
            action="count",
            query=FactQuery(),
            family_id="specific-family",
            field=None,
            search_query=None,
        )
