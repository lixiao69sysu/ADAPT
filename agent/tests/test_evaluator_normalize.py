"""Unit tests for the evaluator-output normalizer used by re-scoring."""

from agent.evaluation_integrity import normalize_evaluator_result


def test_valid_list_of_dicts_passes_through_unchanged():
    payload = [
        {"rubric_idx": "rubric_0", "meetExpectation": True},
        {"rubric_idx": "rubric_1", "meetExpectation": False},
    ]
    assert normalize_evaluator_result(payload) == payload


def test_lone_object_is_wrapped_in_list():
    payload = {"rubric_idx": "rubric_0", "meetExpectation": True}
    assert normalize_evaluator_result(payload) == [payload]


def test_double_wrapped_arrays_are_flattened():
    payload = [
        [{"rubric_idx": "rubric_0"}],
        [{"rubric_idx": "rubric_1"}, {"rubric_idx": "rubric_2"}],
    ]
    normalized = normalize_evaluator_result(payload)
    assert normalized == [
        {"rubric_idx": "rubric_0"},
        {"rubric_idx": "rubric_1"},
        {"rubric_idx": "rubric_2"},
    ]


def test_mixed_list_keeps_dicts_and_flattens_inner_lists():
    payload = [
        {"rubric_idx": "rubric_0"},
        [{"rubric_idx": "rubric_1"}],
        "garbage",
    ]
    normalized = normalize_evaluator_result(payload)
    assert normalized == [
        {"rubric_idx": "rubric_0"},
        {"rubric_idx": "rubric_1"},
    ]


def test_stray_scalars_inside_arrays_are_dropped():
    payload = [
        {"rubric_idx": "rubric_0"},
        [3, 4, {"rubric_idx": "rubric_1"}, [None, 7]],
    ]
    normalized = normalize_evaluator_result(payload)
    assert normalized == [
        {"rubric_idx": "rubric_0"},
        {"rubric_idx": "rubric_1"},
    ]


def test_non_json_scalar_becomes_empty_list():
    assert normalize_evaluator_result("text") == []
    assert normalize_evaluator_result(None) == []
    assert normalize_evaluator_result(3.14) == []


def test_deeply_nested_dicts_are_collected():
    payload = [[{"rubric_idx": "rubric_0"}, [{"rubric_idx": "inner"}]]]
    normalized = normalize_evaluator_result(payload)
    assert normalized == [
        {"rubric_idx": "rubric_0"},
        {"rubric_idx": "inner"},
    ]


def test_recursion_is_depth_bounded():
    payload = []
    node = payload
    for _ in range(20):
        child = [{"rubric_idx": "deep"}]
        node.append(child)
        node = child
    normalized = normalize_evaluator_result(payload)
    assert {"rubric_idx": "deep"} in normalized


def test_meet_expectation_string_true_false_is_coerced():
    payload = [
        {"rubric_idx": "rubric_0", "meetExpectation": "true"},
        {"rubric_idx": "rubric_1", "meetExpectation": "False"},
    ]
    normalized = normalize_evaluator_result(payload)
    assert normalized[0]["meetExpectation"] is True
    assert normalized[1]["meetExpectation"] is False


def test_meet_expectation_non_bool_is_dropped_to_keep_previous_state():
    payload = [
        {"rubric_idx": "rubric_0", "meetExpectation": 5},
        {"rubric_idx": "rubric_1", "meetExpectation": None},
        {"rubric_idx": "rubric_2", "meetExpectation": True},
    ]
    normalized = normalize_evaluator_result(payload)
    assert "meetExpectation" not in normalized[0]
    assert "meetExpectation" not in normalized[1]
    assert normalized[2]["meetExpectation"] is True
