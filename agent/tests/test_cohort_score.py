from scripts.cohort_score import audit


def checkpoint():
    return {"tasks": ["example"], "info": {"num_trials": 4}, "simulations": [
        {"task_id": "example", "trial": i, "reward_info": {"info": {
            "subtask_rewards": {"subtask_0_reward": 1.0}}}} for i in range(4)]}


def test_complete_and_missing_replicate_are_distinguished():
    base = checkpoint()
    assert audit(base, base)["target_achieved"]
    incomplete = checkpoint()
    incomplete["simulations"].pop()
    result = audit(incomplete, base)
    assert result["observed_unit_mean"] == 1
    assert result["official_Avg_at_4"] is None
    assert not result["target_achieved"]


def test_duplicate_and_different_cohort_cannot_pass():
    base = checkpoint()
    duplicate = checkpoint()
    duplicate["simulations"].append(duplicate["simulations"][0])
    assert not audit(duplicate, base)["target_achieved"]
    wrong = checkpoint()
    wrong["tasks"] = ["another"]
    assert not audit(wrong, base)["target_achieved"]


def test_oracle_and_sliced_results_are_ineligible():
    base = checkpoint()
    for flag in ({"memory_type": "groundtruth"}, {"subtask_ids": ["example"]}):
        candidate = checkpoint()
        candidate["info"].update(flag)
        assert not audit(candidate, base)["target_achieved"]
