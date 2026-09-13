"""Content-free per-condition evaluation detail.

Why this exists
---------------
The vendored aggregator keeps only the binary subtask reward
(``personalization_orchestrator.py``: ``breakdown[f"subtask_{i}_reward"] =
ri.reward``), so every saved artifact discards the graded verdict the evaluator
had already computed. At simulation level ``nl_rubrics`` is empty,
``reward_breakdown`` is ``null`` and ``window_evaluations`` is ``None``.

That is why bottleneck inference in this project kept failing. With nothing but
0/1 outcomes per subtask and a ~0.04 two-standard-error band at one trial, every
candidate bottleneck had to be inferred from correlation, and each one was
falsified once actually tested: "the agent never lands a write" (E-053), "the
order is left unpaid", and "the chosen entity violates an explicit instruction
constraint" (violation rate 0.435 among failing writes against 0.500 among
passing ones -- a validator on that signal would be net negative).

What is recorded
----------------
For each rubric condition, only a neutral dimension label and its ``met`` flag.
The condition text is deliberately **not** stored: it is hidden benchmark content,
and the label is sufficient for aggregate diagnosis. What this buys:

- ``fraction_met`` per subtask: a graded, far more sensitive metric than 0/1, so
  a +0.02 change becomes resolvable instead of sitting inside the noise floor;
- ``missed_by_dimension``: which requirement dimension the loss actually sits in;
- ``one_condition_away``: the convertible pool -- failing subtasks that missed by
  exactly one condition.

The label table is a triage heuristic and is documented as such. The headline
number, ``fraction_met``, does not depend on it.
"""

from __future__ import annotations

from typing import Any, Optional

# Priority-ordered: the first matching family wins. Specific families come first
# so that e.g. "靠窗座位" lands on `seat` rather than the broader `entity`.
_DIMENSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("payment", ("支付", "付款", "待支付", "已支付")),
    ("inventory", ("售罄", "库存", "余票", "已售", "quantity为0", "缺货")),
    ("seat", ("座位", "靠窗", "靠过道", "二等座", "一等座", "商务座", "舱位", "硬座", "卧铺")),
    ("route", ("出发地", "目的地", "出发", "到达", "车次", "航班", "中转")),
    ("date", ("日期", "当天", "入住", "送达时间", "出发时间", "次日", "月", "号")),
    ("sugar", ("糖", "甜度")),
    ("temperature", ("温度", "常温", "温热", "热饮", "去冰", "少冰", "多冰", "冰")),
    ("address", ("地址", "送到", "配送至", "配送到")),
    # `rating` must precede `store`: "商家评分" is a rating requirement whose word
    # "商家" would otherwise claim it for the store family.
    ("rating", ("评分", "评价")),
    ("store", ("店铺", "商家", "品牌", "门店", "分店")),
    ("distance", ("距离", "时长", "分钟", "公里", "km")),
    ("price", ("价格", "预算", "价位", "元")),
    # Multi-character markers only: bare measure words like 件/张 also occur in
    # unrelated words ("条件"), which mislabels conditions as quantity.
    ("quantity", ("数量", "份数", "一张", "两张", "一份", "几份", "件数", "杯数")),
    ("preference", ("偏好", "习惯", "常点", "招牌", "仪式感", "已收藏", "喜欢")),
    ("entity", ("商品", "名称", "不应选择", "类别", "套餐", "不应", "选择")),
)

UNAVAILABLE = "unavailable"


def dimension_of(condition_text: Optional[str]) -> str:
    """Neutral label for one rubric condition, or ``other`` when unknown."""
    text = condition_text or ""
    for label, markers in _DIMENSIONS:
        if any(marker in text for marker in markers):
            return label
    return "other"


def condition_record(reward_info: Any) -> dict[str, Any]:
    """Derive one content-free record from a subtask ``RewardInfo``.

    Never returns rubric text. ``status`` is ``available`` when the evaluator
    produced per-condition verdicts, and ``unavailable`` when evaluation failed --
    the latter must be excluded from fractions rather than counted as zero
    (the distinction E-037 exists to preserve).
    """
    checks = getattr(reward_info, "nl_rubrics", None) or []
    reward = getattr(reward_info, "reward", None)
    record: dict[str, Any] = {
        "reward": float(reward) if isinstance(reward, (int, float)) else None,
        "status": UNAVAILABLE,
        "source": None,
        "n_conditions": 0,
        "n_met": 0,
        "fraction_met": None,
        "dimensions": {},
        "missed_dimensions": [],
    }

    if checks:
        met_flags: list[bool] = []
        dims: dict[str, bool] = {}
        for check in checks:
            met = bool(getattr(check, "met", False))
            met_flags.append(met)
            label = dimension_of(getattr(check, "nl_rubric", None))
            # A condition that is not met wins the label so a missed requirement
            # is never masked by a met one sharing its dimension.
            dims[label] = dims.get(label, True) and met
        record.update(
            status="available",
            source="nl_rubrics",
            n_conditions=len(met_flags),
            n_met=sum(1 for m in met_flags if m),
            fraction_met=sum(1 for m in met_flags if m) / len(met_flags),
            dimensions=dims,
            missed_dimensions=sorted(k for k, v in dims.items() if not v),
        )
        return record

    breakdown = getattr(reward_info, "reward_breakdown", None)
    if isinstance(breakdown, dict) and breakdown:
        values = [v for v in breakdown.values() if isinstance(v, (int, float))]
        if values:
            fraction = float(values[0])
            record.update(
                status="available",
                source="reward_breakdown",
                n_met=None,
                fraction_met=fraction,
            )
    return record


def summarize(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Aggregate content-free records into the numbers diagnosis needs."""
    available = [r for r in records.values() if r.get("status") == "available"]
    fractions = [r["fraction_met"] for r in available if r.get("fraction_met") is not None]

    missed_by_dimension: dict[str, int] = {}
    for r in available:
        for label in r.get("missed_dimensions") or []:
            missed_by_dimension[label] = missed_by_dimension.get(label, 0) + 1

    histogram: dict[str, int] = {}
    for f in fractions:
        key = f"{round(f, 1):.1f}"
        histogram[key] = histogram.get(key, 0) + 1

    graded = [
        r
        for r in available
        if isinstance(r.get("n_conditions"), int)
        and isinstance(r.get("n_met"), int)
        and r["n_conditions"] > 0
    ]
    # Failing subtasks that missed by exactly one condition: the convertible pool.
    one_away = sorted(
        key
        for key, r in records.items()
        if r.get("status") == "available"
        and r.get("reward") == 0.0
        and isinstance(r.get("n_conditions"), int)
        and isinstance(r.get("n_met"), int)
        and r["n_conditions"] - r["n_met"] == 1
    )
    per_condition = (
        sum(r["n_met"] for r in graded) / sum(r["n_conditions"] for r in graded)
        if graded
        else None
    )

    return {
        "n_subtasks": len(records),
        "n_available": len(available),
        "n_unavailable": len(records) - len(available),
        "mean_fraction_met": (sum(fractions) / len(fractions)) if fractions else None,
        "per_condition_success_rate": per_condition,
        "fraction_met_histogram": dict(sorted(histogram.items())),
        "missed_by_dimension": dict(
            sorted(missed_by_dimension.items(), key=lambda kv: -kv[1])
        ),
        "one_condition_away": one_away,
        "n_one_condition_away": len(one_away),
        "per_subtask": records,
    }
