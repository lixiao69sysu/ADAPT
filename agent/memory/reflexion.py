"""Legacy lesson data structures retained for compatibility tests.

This module is not wired into benchmark execution and must never consume an
evaluator reward. Runtime self-improvement lives in ``agent.lessons`` and uses
only agent-visible user corrections, tool errors, validator rejections,
repeated searches, missed writes and incomplete payment states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

# --- Chinese tokenization ---------------------------------------------------
# Mirrors agent.memory.retrieval's strategy (jieba, else character bigrams),
# kept local so this module stays independent of files the running processes
# imported. Small duplication is the price of not touching retrieval.py now.
try:
    import jieba
    jieba.setLogLevel(60)   # suppress INFO logs
    _JIEBA_AVAILABLE = True
except ImportError:
    _JIEBA_AVAILABLE = False


def _tokenize(text: str) -> Set[str]:
    """CJK tokens for lesson/query matching (jieba or bigram fallback)."""
    if _JIEBA_AVAILABLE:
        tokens = set(jieba.lcut(text or ""))
        # Keep only CJK tokens with len >= 2 (single chars are too noisy).
        tokens = {t for t in tokens if len(t) >= 2 and any("一" <= c <= "鿿" for c in t)}
    else:
        chars = [c for c in (text or "") if "一" <= c <= "鿿"]
        tokens = {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}
    return tokens


# --- Failure-mode taxonomy --------------------------------------------------

FAILURE_MODE_DRIFT = "drift_blind_spot"       # 口味/规格/地址约束没执行到位
FAILURE_MODE_MISMATCH = "preference_mismatch" # 选择/下单与偏好画像不符
FAILURE_MODE_TOOL = "tool_error"              # 工具连续出错 / 参数问题
FAILURE_MODE_PROACTIVE = "proactive_miss"     # 该问没问,直接替用户决定
FAILURE_MODE_DATE = "date_reasoning"          # 日期/时间推理错误
FAILURE_MODE_GENERIC = "generic"

FAILURE_MODES = (
    FAILURE_MODE_DRIFT,
    FAILURE_MODE_MISMATCH,
    FAILURE_MODE_TOOL,
    FAILURE_MODE_PROACTIVE,
    FAILURE_MODE_DATE,
    FAILURE_MODE_GENERIC,
)

_DRIFT_MARKERS = ("地址", "糖", "甜", "冰", "热", "辣", "口味", "规格", "备注", "忌口")
_DATE_MARKERS = (
    "日期", "几号", "明天", "后天", "昨天", "今天", "礼拜", "几点", "时间",
    "入住", "离店", "下周", "这周", "上周", "周末", "周六", "周日", "周一",
    "周二", "周三", "周四", "周五",
)


def classify_failure(
    instruction: str,
    skill: Optional[List[str]] = None,
    last_action: str = "",
    termination: str = "",
) -> str:
    """Best-effort failure-mode classification from the failure scene.

    Order matters: hard tool termination and proactive skills outrank content
    markers (a proactive task can also mention dates).
    """
    if "too_many_errors" in (termination or ""):
        return FAILURE_MODE_TOOL
    if skill and any("proactive" in (s or "") for s in skill):
        return FAILURE_MODE_PROACTIVE
    if any(m in (instruction or "") for m in _DATE_MARKERS):
        return FAILURE_MODE_DATE
    if any(m in (instruction or "") for m in _DRIFT_MARKERS):
        return FAILURE_MODE_DRIFT
    if last_action:
        return FAILURE_MODE_MISMATCH
    return FAILURE_MODE_GENERIC


# --- Lesson store -----------------------------------------------------------

@dataclass
class ReflexionLesson:
    """A single distilled lesson from a failed subtask."""

    id: int
    domain: str                 # delivery / instore / ota (may be "")
    subtask_id: str             # e.g. "sub_B048564_3"
    instruction: str            # the failed instruction (truncated)
    lesson: str                 # one-sentence, actionable, 30-60 chars
    failure_mode: str = FAILURE_MODE_GENERIC
    importance: float = 6.0     # lessons are high-value by construction
    timestamp: str = ""         # YYYY-MM-DD HH:MM:SS
    raw_facts: str = ""         # evidence: memory snapshot / last action

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "domain": self.domain,
            "subtask_id": self.subtask_id,
            "instruction": self.instruction[:200],
            "lesson": self.lesson,
            "failure_mode": self.failure_mode,
            "importance": self.importance,
            "timestamp": self.timestamp,
            "raw_facts": self.raw_facts[:300],
        }


class ReflexionEngine:
    """Dedicated lesson store with query-aware retrieval + soft-hint rendering.

    Separate from MemoryStream by design: lessons are the agent's meta-learning
    about its own execution, not user preferences. They get no lifecycle decay
    or drift suppression (a lesson stays useful until superseded by a pass).
    """

    def __init__(self, max_lessons: int = 40, top_k: int = 5) -> None:
        self.max_lessons = max_lessons
        self.top_k = top_k
        self._lessons: List[ReflexionLesson] = []
        self._next_id = 0
        self._domain_detector = None

    # -- write ---------------------------------------------------------------

    def add(
        self,
        domain: str,
        subtask_id: str,
        instruction: str,
        lesson: str,
        failure_mode: str = FAILURE_MODE_GENERIC,
        importance: float = 6.0,
        timestamp: str = "",
        raw_facts: str = "",
    ) -> ReflexionLesson:
        """Store a lesson (skip empties, dedupe exact text, cap the store)."""
        lesson = (lesson or "").strip()
        if not lesson:
            raise ValueError("lesson must be non-empty")
        if any(l.lesson == lesson for l in self._lessons):
            return self._lessons[-1]   # idempotent: same lesson already known
        ev = ReflexionLesson(
            id=self._next_id,
            domain=domain,
            subtask_id=subtask_id,
            instruction=instruction,
            lesson=lesson,
            failure_mode=failure_mode,
            importance=importance,
            timestamp=timestamp,
            raw_facts=raw_facts,
        )
        self._next_id += 1
        self._lessons.append(ev)
        self._prune()
        return ev

    def all(self) -> List[ReflexionLesson]:
        return list(self._lessons)

    def __len__(self) -> int:
        return len(self._lessons)

    def reset(self) -> None:
        self._lessons = []
        self._next_id = 0

    def _prune(self) -> None:
        if len(self._lessons) > self.max_lessons:
            self._lessons = sorted(
                self._lessons, key=lambda l: l.importance, reverse=True
            )[: self.max_lessons]

    # -- retrieve ------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        domain: Optional[str] = None,
        k: Optional[int] = None,
    ) -> List[ReflexionLesson]:
        """Top-k lessons relevant to the current instruction.

        Scoring = domain gate (hard suppress cross-domain) + keyword overlap,
        with importance as tiebreak. Relevance floor keeps the block from
        filling with noise when a query matches nothing.
        """
        qtok = _tokenize(query or "")
        if not qtok:
            return []   # empty/stopword query: don't inject stale lessons
        qdom = domain or self._detect_domain(query)
        scored: List[tuple[float, ReflexionLesson]] = []
        for lesson in self._lessons:
            s = self._score(lesson, qtok, qdom)
            if s > 0:
                scored.append((s, lesson))
        scored.sort(key=lambda x: (x[0], x[1].importance), reverse=True)
        return [l for _, l in scored[: (k or self.top_k)]]

    def _score(self, lesson: ReflexionLesson, qtok: Set[str], qdom: Optional[str]) -> float:
        if qdom and lesson.domain and lesson.domain != qdom:
            return 0.0   # never inject a delivery lesson into an OTA task
        text = f"{lesson.instruction} {lesson.lesson} {lesson.failure_mode}"
        overlap = qtok & _tokenize(text)
        if not overlap:
            return 0.05   # weak same-domain fallback (few lessons exist early)
        return min(1.0, 0.5 + 0.08 * len(overlap))

    def _detect_domain(self, query: str) -> Optional[str]:
        if self._domain_detector is None:
            try:
                from agent.memory.retrieval import RetrievalScorer
                self._domain_detector = RetrievalScorer()
            except Exception:
                self._domain_detector = False
        if not self._domain_detector:
            return None
        return self._domain_detector.domain(query)

    # -- render --------------------------------------------------------------

    def format_injection(self, lessons: List[ReflexionLesson]) -> str:
        """Render top lessons as a soft 【经验教训】 block for read() output."""
        if not lessons:
            return ""
        lines = ["【经验教训】"]
        for l in lessons:
            src = l.subtask_id or (l.instruction[:24] + "…")
            lines.append(f"- {l.lesson}（曾失败：{src}）")
        lines.append("以上为过往任务失败的经验，仅供参考；与本次指令冲突时以本次指令为准。")
        return "\n".join(lines)

    # -- persistence ---------------------------------------------------------

    def to_dict(self) -> List[Dict[str, object]]:
        return [l.to_dict() for l in self._lessons]

    def from_dict(self, items: List[Dict[str, object]]) -> None:
        """Restore lessons from a serialized list (idempotent reset + fill)."""
        self.reset()
        for it in items or []:
            try:
                self.add(
                    domain=str(it.get("domain", "")),
                    subtask_id=str(it.get("subtask_id", "")),
                    instruction=str(it.get("instruction", "")),
                    lesson=str(it.get("lesson", "")),
                    failure_mode=str(it.get("failure_mode", FAILURE_MODE_GENERIC)),
                    importance=float(it.get("importance", 6.0)),
                    timestamp=str(it.get("timestamp", "")),
                    raw_facts=str(it.get("raw_facts", "")),
                )
            except (ValueError, TypeError):
                continue   # skip malformed entries; never crash the resume


# --- Lesson distillation ----------------------------------------------------
# Two paths: an LLM prompt (wiring phase, richer lessons) and a zero-cost
# heuristic fallback (no API, unit-testable, works offline).

def distill_lesson_prompt(
    instruction: str,
    domain: str,
    last_action: str = "",
    memory_snapshot: str = "",
) -> str:
    """Build the LLM reflection prompt for a failed subtask scene."""
    return (
        "你是 Agent 自进化反思器。给定一个失败子任务的现场，写出一条具体的、可复用的执行教训。\n"
        "要求：\n"
        "1. 对照指令的每个约束点，具体指出哪里没做好（不要泛泛而谈）；\n"
        "2. 给出下次遇到同类任务时应做的动作；\n"
        "3. 只输出一句话，30-60 字；不要复述用户偏好，不要解释。\n\n"
        f"## 领域：{domain}\n"
        f"## 失败指令：{instruction}\n"
        f"## 注入的记忆：{memory_snapshot or '（空）'}\n"
        f"## 最后动作：{last_action or '（无工具调用）'}\n\n"
        "## 输出：只输出教训文本本身。"
    )


def distill_lesson_heuristic(
    instruction: str,
    domain: str,
    last_action: str = "",
    termination: str = "",
    skill: Optional[List[str]] = None,
    memory_snapshot: str = "",
) -> str:
    """Zero-cost lesson fallback keyed on the classified failure mode."""
    del memory_snapshot  # heuristic doesn't need the snapshot text
    mode = classify_failure(instruction, skill, last_action, termination)
    if mode == FAILURE_MODE_TOOL:
        return "工具连续出错导致子任务终止：调用工具前核对店名/商品名等参数是否与任务完全一致，避免模糊参数。"
    if mode == FAILURE_MODE_PROACTIVE:
        return "proactive 类任务：先向用户确认关键需求（口味/规格/预算/时间）再执行，不要直接替用户做决定。"
    if mode == FAILURE_MODE_DATE:
        return "涉及日期/时间的任务：先核对日历与行程日期再下单，避免订错日期。"
    if mode == FAILURE_MODE_DRIFT:
        return "涉及口味/规格/地址约束的任务：严格执行偏好画像中的甜度/冰度/忌口等要求，不要想当然。"
    if mode == FAILURE_MODE_MISMATCH:
        return "该类型任务曾因选择与偏好不符失败：选择商家/商品时优先匹配用户偏好画像。"
    return "该类型任务曾失败：执行前逐条核对指令约束，并优先匹配用户偏好画像。"
