"""Real-site case library plans stay inside the structure-only plan contract.

The files under docs/research/case-library/plans are plan data observed on live
official sites; a reusable one must pass the same validators a recon plan does.
"""
import json
from pathlib import Path

import pytest

from server.agent_recon import validate_model_plan
from server.browser_batch_tool import _validate_plan

PLANS = sorted((Path(__file__).resolve().parents[1] / "docs/research/case-library/plans").glob("*.json"))
SUPPORT = {"supported", "supported_after_fix", "partially_supported", "unsupported_scope",
           "unsupported_handed_over", "unsupported_mechanism", "defect_repro_only"}


def test_library_is_not_empty():
    assert PLANS


@pytest.mark.parametrize("path", PLANS, ids=lambda path: path.stem)
def test_case_plan_matches_the_recon_contract(path):
    case = json.loads(path.read_text(encoding="utf-8"))
    assert case["case_id"] == path.stem and case["support"] in SUPPORT
    assert case["reusable"] == case["support"].startswith("supported")
    plan = case["plan"]
    _validate_plan(plan)
    validate_model_plan(plan, school=plan["school"], college=plan["college"])

# This component fixture stipulates verified source purpose; purpose integration is unmocked elsewhere.
pytestmark = pytest.mark.verified_source_boundary
