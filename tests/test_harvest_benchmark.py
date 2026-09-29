"""Benchmark denominators cannot silently drop difficult random samples."""
import pytest

from server.harvest_benchmark import summarize_benchmark


def manifest():
    return {"sampling": {"seed": 20260927, "frame_sha256": "a" * 64},
            "cases": [{"id": str(i), "school": f"样本大学{i}"} for i in range(6)]}


def test_missing_and_failed_samples_remain_in_denominator():
    outcomes = [{"id": "0", "stage": "harvest", "evidence_mode": "live_browser",
                 "report": {"delivery_ready": True, "total": 12}},
                {"id": "1", "stage": "discovery", "failure_code": "navigation_failed"},
                {"id": "2", "stage": "harvest", "report": {"delivery_ready": False, "total": 4,
                              "unresolved": [{"status": "card_mismatch"}]}}]
    result = summarize_benchmark(manifest(), outcomes)
    assert result["selected_cases"] == 6
    assert result["reported_cases"] == 3
    assert result["not_tested_cases"] == 3
    assert result["collector_ready_cases"] == 1
    assert result["independently_verified_cases"] == 0
    assert result["generalization_proven"] is False
    assert len(result["cases"]) == 6


def test_replay_and_incomplete_oracle_do_not_count_as_verified_live_collection():
    checked = {"reviewed_independently": True, "complete_roster_review": True,
               "expected_observations": 12, "identity_set_matches": True,
               "sampled_profile_count": 2, "sampled_fulltext_matches": True,
               "sampled_fields_match": True,
               "sampled_clean_body_match": True,
               "evidence_paths": ["independent-oracle.json"]}
    outcomes = [{"id": str(i), "stage": "harvest", "evidence_mode": mode,
                 "report": {"delivery_ready": True, "total": 12}, "oracle": oracle}
                for i, mode, oracle in [(0, "saved_replay", checked),
                    (1, "live_browser", {**checked, "complete_roster_review": False}),
                    (2, "live_browser", checked)]]
    result = summarize_benchmark(manifest(), outcomes)
    assert result["collector_ready_cases"] == 3
    assert result["live_browser_cases"] == 2
    assert result["independently_verified_cases"] == 1
    assert result["cases"][2]["independently_verified"] is True


def test_profile_field_errors_block_independent_verification_even_with_full_text():
    outcome = {"id": "0", "evidence_mode": "live_browser",
               "report": {"delivery_ready": True, "total": 12},
               "oracle": {"reviewed_independently": True, "complete_roster_review": True,
                          "expected_observations": 12, "identity_set_matches": True,
                          "sampled_profile_count": 2, "sampled_fulltext_matches": True,
                          "sampled_fields_match": False, "evidence_paths": ["oracle.json"]}}
    assert summarize_benchmark(manifest(), [outcome])["independently_verified_cases"] == 0


def test_preserving_fulltext_with_template_noise_does_not_pass_clean_body_review():
    outcome = {"id": "0", "evidence_mode": "live_browser",
               "report": {"delivery_ready": True, "total": 12},
               "oracle": {"reviewed_independently": True, "complete_roster_review": True,
                          "expected_observations": 12, "identity_set_matches": True,
                          "sampled_profile_count": 2, "sampled_fulltext_matches": True,
                          "sampled_fields_match": True, "sampled_clean_body_match": False,
                          "evidence_paths": ["oracle.json"]}}
    assert summarize_benchmark(manifest(), [outcome])["independently_verified_cases"] == 0


@pytest.mark.parametrize("outcomes", [[{"id": "absent"}], [{"id": "0"}, {"id": "0"}]])
def test_unselected_or_duplicate_results_are_rejected(outcomes):
    with pytest.raises(ValueError):
        summarize_benchmark(manifest(), outcomes)


def test_missing_model_measurement_is_unknown_not_zero():
    result = summarize_benchmark(manifest(), [{"id": "0", "stage": "harvest"},
                                            {"id": "1", "model_usage": {"model_calls": 0, "tokens": 0}}])
    assert result["model_usage"]["unknown_cases"] == 1
    assert result["model_usage"]["reported_model_calls"] == 0
    assert result["cases"][0]["model_usage"] is None
