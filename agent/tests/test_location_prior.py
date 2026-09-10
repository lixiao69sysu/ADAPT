"""Observable proximity and rating prior in candidate ranking (E-038).

The user's registered home address and each candidate's own address are both
observable. Ranking must use them to break an evidence tie, so a candidate in
another district is not presented as the top recommendation merely because it
was observed first.
"""

from __future__ import annotations

from agent.decision import Candidate, CandidateLedger, DecisionCard, TaskSpec
from agent.runtime.location import (
    CITY_MATCH,
    DISTRICT_MATCH,
    LOCATION_NEUTRAL,
    administrative_tokens,
    candidate_rating,
    home_tokens,
    location_rank,
)

PROFILE = {
    "user_id": "U-TEST",
    "常住地": "四川省成都市",
    "常住住址": "四川省成都市温江区寿安镇火星村462号",
}


def candidate(
    candidate_id: str,
    name: str,
    address: str,
    score: float,
    *,
    observed_turn: int = 1,
) -> Candidate:
    raw = (
        f"Shop(shop_name={name}, shop_id={candidate_id}, score={score}, "
        f"location=address='{address}' longitude:104.0,latitude=30.6)"
    )
    return Candidate(
        candidate_id,
        "shop",
        name,
        raw,
        "instore_shop_search_recommend",
        {"score": str(score), "shop_name": name, "location": f"address='{address}'"},
        [],
        None,
        None,
        observed_turn,
    )


def test_administrative_tokens_split_a_full_address():
    tokens = administrative_tokens("四川省成都市温江区寿安镇火星村462号")
    assert "成都市" in tokens
    assert "温江区" in tokens
    assert "寿安镇" in tokens
    assert "四川省成都市" not in tokens


def test_home_tokens_read_only_the_registered_address():
    assert home_tokens(PROFILE) == ["成都市", "温江区", "寿安镇"]
    assert home_tokens({}) == []
    assert home_tokens(None) == []


def test_location_rank_prefers_district_then_city():
    same_district = candidate(
        "S1_1", "小红帽草莓采摘园(温江涌泉店)", "四川省成都市温江区涌泉街道", 4.9
    )
    same_city = candidate(
        "S1_2", "花间莓舍草莓采摘园(双流黄甲店)", "四川省成都市双流区黄甲街道", 4.9
    )
    other_city = candidate(
        "S1_3", "普吉岛卡伦海滩洲际度假酒店", "泰国普吉岛卡伦海滩路150号", 4.9
    )
    tokens = home_tokens(PROFILE)
    assert location_rank(same_district, tokens) == DISTRICT_MATCH
    assert location_rank(same_city, tokens) == CITY_MATCH
    assert location_rank(other_city, tokens) == LOCATION_NEUTRAL
    assert location_rank(same_district, []) == LOCATION_NEUTRAL


def test_location_rank_matches_an_abbreviated_branch_name():
    abbreviated = candidate(
        "S1_4", "甜蜜莓园草莓采摘(温江万春店)", "四川省成都市温江区万春镇", 4.7
    )
    assert location_rank(abbreviated, home_tokens(PROFILE)) == DISTRICT_MATCH


def test_a_same_named_town_in_another_county_is_not_a_match():
    """温江区寿安镇 and 蒲江县寿安街道 are different places."""
    elsewhere = candidate(
        "S1_7",
        "绿野仙踪草莓采摘园(蒲江寿安店)",
        "四川省成都市蒲江县寿安街道",
        4.7,
    )
    assert location_rank(elsewhere, home_tokens(PROFILE)) == CITY_MATCH


def test_candidate_rating_reads_the_published_score():
    assert candidate_rating(candidate("S1_5", "店", "地址", 4.6)) == 4.6
    assert candidate_rating(Candidate("S1_6", "shop", "店", "raw", "tool")) == 0.0


def test_tie_is_broken_by_proximity_then_rating():
    ledger = CandidateLedger()
    ledger.home_tokens = home_tokens(PROFILE)
    # Observed first, but three districts away from home.
    ledger.candidates["S1_FAR"] = candidate(
        "S1_FAR",
        "莓满园草莓采摘庄园(青白江城厢店)",
        "四川省成都市青白江区城厢镇",
        4.9,
        observed_turn=1,
    )
    ledger.candidates["S1_NEAR_LOW"] = candidate(
        "S1_NEAR_LOW",
        "甜蜜莓园草莓采摘(温江万春店)",
        "四川省成都市温江区万春镇",
        4.7,
        observed_turn=2,
    )
    ledger.candidates["S1_NEAR_HIGH"] = candidate(
        "S1_NEAR_HIGH",
        "小红帽草莓采摘园(温江涌泉店)",
        "四川省成都市温江区涌泉街道",
        4.9,
        observed_turn=3,
    )
    ranked = ledger.shortlist(
        DecisionCard(), limit=3
    )
    assert [item.candidate_id for item in ranked] == [
        "S1_NEAR_HIGH",
        "S1_NEAR_LOW",
        "S1_FAR",
    ]


def test_proximity_never_outranks_preference_evidence():
    ledger = CandidateLedger()
    ledger.home_tokens = home_tokens(PROFILE)
    ledger.candidates["S1_FAR"] = candidate(
        "S1_FAR", "花溪谷有机草莓庄园", "四川省成都市新都区", 4.5, observed_turn=5
    )
    ledger.candidates["S1_NEAR"] = candidate(
        "S1_NEAR", "小红帽草莓采摘园(温江涌泉店)", "四川省成都市温江区", 5.0
    )
    card = DecisionCard(prefer=["花溪谷有机草莓庄园"])
    ranked = ledger.shortlist(card, limit=2)
    assert ranked[0].candidate_id == "S1_FAR"


def test_ranking_without_home_context_is_unchanged():
    ledger = CandidateLedger()
    ledger.candidates["S2_A"] = candidate(
        "S2_A", "普吉岛奈扬海滩万豪度假酒店", "泰国普吉岛奈扬海滩路22号", 4.6
    )
    ledger.candidates["S2_B"] = candidate(
        "S2_B", "普吉岛卡马拉湾花园度假酒店", "泰国普吉岛卡马拉海滩路156号", 4.9
    )
    ranked = ledger.shortlist(DecisionCard(), limit=2)
    assert [item.candidate_id for item in ranked] == ["S2_B", "S2_A"]


def test_home_tokens_are_not_read_from_the_instruction():
    spec = TaskSpec.compile("周末又想去摘草莓了，你给我推荐一个适合的采摘园呗")
    assert home_tokens(PROFILE)[0] == "成都市"
    # The task spec itself carries no location profile.
    assert not any("温江" in value for value in spec.must)


def product_row(candidate_id: str, name: str, parent_shop: str, score: float) -> Candidate:
    raw = (
        f"ShopProduct(shop_id={parent_shop}, product_id={candidate_id}, "
        f"name={name}, price={score}, quantity=5, tags=['团购券'])"
    )
    return Candidate(
        candidate_id,
        "product",
        name,
        raw,
        "instore_product_search_recommend",
        {"product_name": name, "shop_id": parent_shop},
        [parent_shop],
    )


def test_product_inherits_its_shop_proximity():
    ledger = CandidateLedger()
    ledger.home_tokens = home_tokens(PROFILE)
    ledger.candidates["S3_NEAR_SHOP"] = candidate(
        "S3_NEAR_SHOP", "通络堂养生按摩(温江店)", "四川省成都市温江区", 4.9
    )
    ledger.candidates["S3_FAR_SHOP"] = candidate(
        "S3_FAR_SHOP", "青白江养生按摩", "四川省成都市青白江区", 4.9
    )
    ledger.candidates["S3_NEAR"] = product_row(
        "S3_NEAR", "全身按摩90分钟团购券", "S3_NEAR_SHOP", 188.0
    )
    ledger.candidates["S3_FAR"] = product_row(
        "S3_FAR", "全身按摩90分钟团购券", "S3_FAR_SHOP", 168.0
    )
    ranked = ledger.shortlist(DecisionCard(), limit=2)
    assert [item.candidate_id for item in ranked] == ["S3_NEAR", "S3_FAR"]


class _Debug:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, **payload) -> None:
        self.events.append({"event": event, **payload})


def build_recommender(ledger: CandidateLedger, card: DecisionCard):
    from agent.adapt_agent import ADAPTAgent
    from agent.runtime.state import RuntimePhase, TaskRuntime

    agent = ADAPTAgent.__new__(ADAPTAgent)
    agent.ledger = ledger
    agent.decision_card = card
    agent.task_spec = TaskSpec.compile("推荐一个适合的采摘园")
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.runtime.phase = RuntimePhase.SELECT
    agent._recommendation_delivered = False
    agent.home_tokens = home_tokens(PROFILE)
    agent.debug = _Debug()
    return agent


def strawberry_ledger() -> CandidateLedger:
    ledger = CandidateLedger()
    ledger.home_tokens = home_tokens(PROFILE)
    far = candidate(
        "S4_FAR", "绿野仙踪草莓采摘园(双流店)", "四川省成都市双流区黄水镇", 4.5
    )
    far.raw += " tags=['草莓采摘', '亲子游', '周末好去处', '采摘园']"
    near = candidate(
        "S4_NEAR", "小红帽草莓采摘园(温江涌泉店)", "四川省成都市温江区涌泉街道", 4.9
    )
    near.raw += " tags=['采摘园', '品种丰富', '绿色健康']"
    ledger.candidates["S4_FAR"] = far
    ledger.candidates["S4_NEAR"] = near
    return ledger


def test_non_decisive_coverage_does_not_hide_a_nearer_option():
    """A weak pool match must not drop a nearer, better-rated candidate."""
    agent = build_recommender(
        strawberry_ledger(), DecisionCard(preference_pool=["草莓采摘"])
    )
    message = agent._framework_recommendation()
    assert message is not None
    assert "小红帽草莓采摘园(温江涌泉店)" in message.content


def test_decisive_preference_still_leads_the_recommendation():
    """A decisive remembered preference keeps its precedence (E-038 boundary)."""
    agent = build_recommender(
        strawberry_ledger(), DecisionCard(prefer=["绿野仙踪草莓采摘园"])
    )
    message = agent._framework_recommendation()
    assert message is not None
    assert "绿野仙踪草莓采摘园(双流店)" in message.content
    assert "小红帽" not in message.content
