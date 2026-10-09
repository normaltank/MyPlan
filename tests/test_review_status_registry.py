"""Review status codes have one definition each and agree with the recovery policy."""
import ast
from pathlib import Path

from server import (html_email_review, identity_review, image_table_review, media_kind_review, name_review,
                    recovery_policy, review_registry, task_image_review)

ROSTER = Path(__file__).resolve().parents[1] / "server" / "image_table_roster.py"
# Field names and exclusion markers in the roster that share the prefix but are not issue codes.
ROSTER_NON_STATUS = {"table_row", "table_section_heading"}


def review_codes():
    return {code for code, action in recovery_policy.POLICIES.items() if action == "review"}


def table_literals(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.startswith(image_table_review.ISSUE_PREFIX)}


def test_every_declared_review_status_is_a_review_code():
    declared = {name_review.STATUS, identity_review.STATUS, media_kind_review.STATUS,
                *html_email_review.STATUSES, *image_table_review.STATUSES, *review_registry.STATUSES}
    assert declared - review_codes() == set()


def test_table_review_statuses_match_recovery_policy():
    prefixed = {code for code in review_codes() if code.startswith(image_table_review.ISSUE_PREFIX)}
    assert image_table_review.STATUSES == prefixed


def test_task_review_takes_table_statuses_from_the_table_module():
    assert image_table_review.STATUSES <= review_registry.STATUSES
    assert table_literals(task_image_review.__file__) == table_literals(review_registry.__file__) == set()


def test_every_table_status_the_roster_raises_is_registered():
    raised = table_literals(ROSTER) - ROSTER_NON_STATUS
    assert raised, "roster scan found no table statuses"
    assert raised - set(recovery_policy.POLICIES) == set()
    assert {code for code in raised if recovery_policy.POLICIES[code] == "review"} <= image_table_review.STATUSES
    assert all(code.startswith(image_table_review.ISSUE_PREFIX) for code in image_table_review.STATUSES)
