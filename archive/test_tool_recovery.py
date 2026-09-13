"""Zero-model tests for the archived tool-failure guard-rail.

`tool_recovery.py` was carved out of the frozen controller so a stock skeleton
could use it too, and it is tested here on its own -- importing nothing from
`agent.runtime`. It has **no production call site**, so it lives in `archive/`
and these tests are not part of the `agent/tests` gate. See `archive/README.md`.

No API call is made.
"""

from __future__ import annotations

import archive.tool_recovery as tr


def _fail_create(ledger: tr.ToolFailureLedger, address: str) -> None:
    ledger.register_proposal("c1", "create_delivery_order", {"address": address}, True)
    ledger.observe_result(
        "c1", "create_delivery_order", "Longitude and latitude not found for address", True
    )


def _resolve(ledger: tr.ToolFailureLedger, prefix: str) -> None:
    ledger.register_proposal(
        "r1", "address_to_longitude_latitude", {"address": prefix}, False
    )
    ledger.observe_result(
        "r1", "address_to_longitude_latitude", "Longitude: 104.7, Latitude: 31.5", False
    )


# ── independence ─────────────────────────────────────────────────────────


def test_module_imports_nothing_from_the_controller():
    """The whole point of the extraction: no `agent.runtime` dependency."""
    source = __import__("pathlib").Path(tr.__file__).read_text(encoding="utf-8")
    assert "agent.runtime" not in source
    assert "agent.adapt_agent" not in source


def test_is_write_tool_matches_the_codebase_convention():
    assert tr.is_write_tool("create_delivery_order")
    assert tr.is_write_tool("create_hotel_order")
    assert tr.is_write_tool("instore_book")
    assert tr.is_write_tool("instore_reservation")
    # a payment is a later step, not the write itself
    assert not tr.is_write_tool("pay_delivery_order")
    assert not tr.is_write_tool("address_to_longitude_latitude")
    assert not tr.is_write_tool("")


# ── signatures and error classes ─────────────────────────────────────────


def test_signature_ignores_free_text_and_normalizes_whitespace():
    a = tr.attempt_signature("create_x", {"address": "a  b", "note": "hello"})
    b = tr.attempt_signature("create_x", {"address": "a b", "note": "different"})
    assert a == b
    assert tr.attempt_signature("create_x", {"address": "a b"}) != tr.attempt_signature(
        "create_y", {"address": "a b"}
    )


def test_error_classes_are_derived_from_the_environment_message():
    assert tr.classify_error("Longitude and latitude not found for address X") == (
        "address_not_resolvable"
    )
    assert tr.classify_error("Store not found") == "not_found"
    assert tr.classify_error("missing required argument") == "missing_argument"
    assert tr.classify_error("invalid argument") == "invalid_argument"
    assert tr.classify_error("something else") == "tool_error"


# ── the guard-rail behaviour ─────────────────────────────────────────────


def test_repeated_identical_write_is_rejected():
    ledger = tr.ToolFailureLedger(max_identical_failures=2)
    args = {"address": "郑州市金水区某路1号"}
    assert ledger.rejection_reason("create_delivery_order", args, True) == ""
    for i in range(2):
        ledger.register_proposal(f"c{i}", "create_delivery_order", args, True)
        ledger.observe_result(f"c{i}", "create_delivery_order", "tool_error", True)
    assert "already failed 2 times" in ledger.rejection_reason(
        "create_delivery_order", args, True
    )


def test_a_proven_bad_address_is_recovered_from_a_proven_good_prefix():
    ledger = tr.ToolFailureLedger()
    original = "四川省成都市武侯区一环路南一段24号四川大学望江校区学生宿舍21栋301"
    prefix = "四川省成都市武侯区一环路南一段24号四川大学望江校区学生宿舍"
    _fail_create(ledger, original)
    _resolve(ledger, prefix)

    arguments = {"address": original}
    recovery = ledger.recover("create_delivery_order", arguments, True)

    assert recovery is not None, "a successfully resolved prefix must be usable"
    assert recovery.recovered_value == prefix
    assert arguments["address"] == prefix, "recovery mutates the call in place"


def test_nothing_is_invented_when_no_prefix_was_ever_resolved():
    ledger = tr.ToolFailureLedger()
    original = "四川省成都市武侯区一环路南一段24号四川大学望江校区学生宿舍21栋301"
    _fail_create(ledger, original)
    # no successful resolver result -> nothing is proven, so nothing is offered
    assert ledger.recover("create_delivery_order", {"address": original}, True) is None


def test_non_write_calls_are_never_recovered():
    ledger = tr.ToolFailureLedger()
    original = "四川省成都市武侯区一环路南一段24号四川大学望江校区学生宿舍21栋301"
    prefix = "四川省成都市武侯区一环路南一段24号四川大学望江校区学生宿舍"
    _fail_create(ledger, original)
    _resolve(ledger, prefix)
    assert ledger.recover("address_to_longitude_latitude", {"address": original}, False) is None


def test_address_probe_strips_only_building_suffixes_and_fires_once():
    ledger = tr.ToolFailureLedger()
    original = "郑州市金水区国基路166号3号楼2单元301室"
    _fail_create(ledger, original)

    probe = ledger.next_address_probe()
    assert probe is not None
    assert probe.probe_value == "郑州市金水区国基路166号"
    assert probe.tool_name == "address_to_longitude_latitude"

    ledger.register_proposal(
        "p1", probe.tool_name, {"address": probe.probe_value}, False
    )
    assert ledger.next_address_probe() is None, "each probe is attempted at most once"


def test_a_recovered_attempt_replays_the_same_call_with_only_the_bad_input_changed():
    ledger = tr.ToolFailureLedger()
    original = "四川省成都市武侯区一环路南一段24号四川大学望江校区学生宿舍21栋301"
    prefix = "四川省成都市武侯区一环路南一段24号四川大学望江校区学生宿舍"
    ledger.register_proposal(
        "c1",
        "create_delivery_order",
        {"address": original, "product_ids": ["P1"]},
        True,
    )
    ledger.observe_result(
        "c1", "create_delivery_order", "Longitude and latitude not found for address", True
    )
    _resolve(ledger, prefix)

    recovered = ledger.recovered_create_attempt()

    assert recovered is not None
    assert recovered.arguments["address"] == prefix
    assert recovered.arguments["product_ids"] == ["P1"], "untouched arguments are frozen"


def test_reset_clears_every_kind_of_evidence():
    ledger = tr.ToolFailureLedger()
    _fail_create(ledger, "郑州市某路1号")
    ledger.reset()
    assert ledger.next_address_probe() is None
    assert ledger.failure_count("create_delivery_order", {"address": "郑州市某路1号"}) == 0
    assert ledger.recovered_create_attempt() is None


def test_ledger_is_not_shared_across_instances():
    a = tr.ToolFailureLedger()
    b = tr.ToolFailureLedger()
    _fail_create(a, "郑州市某路1号")
    assert b.failure_count("create_delivery_order", {"address": "郑州市某路1号"}) == 0
