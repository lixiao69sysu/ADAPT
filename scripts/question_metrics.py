"""Question metrics computed the same way for both arms.

The arm's own counters (questions_committed / answers_resolved_to_a_value) are
ADAPT instrumentation and have no baseline counterpart, which is why the panel
first showed "n/a" for the baseline. That was too quick: the stock skeleton asks
the user too -- its prompt tells it to ask whenever a tool parameter is unknown
-- so a text-level definition can be applied identically to both arms.

Definitions (mechanical, applied to every subtask of both checkpoints)
---------------------------------------------------------------------
提问轮        an assistant message addressed to the user that contains '?' or
              '？' and carries no tool call. Tool-call turns are excluded so a
              search result is never mistaken for a question.
回答利用率      for each question turn, take the next user message. The answer
              counts as UTILISED if any >=2-character CJK run or >=4-character
              alphanumeric token from it appears in a later tool-call argument
              inside the same subtask -- i.e. the answer actually reached a call.
              A delegation such as "随便，你看着办吧" therefore counts as not
              utilised, which is the correct reading.
提问命中率      utilised questions / question turns.

The arm's stricter engine counter (3 of 11 questions resolved into a slot value)
is reported alongside; it measures the engine's own bookkeeping, not what
reached a call, and is not comparable to the baseline.

Baseline is taken over all four trials: the four trials are four draws of one
script, so a single slice is not a comparator.

Zero-model; reads saved artifacts only.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ARM = pathlib.Path("data/simulations/adapt_dev_1t.json")
BASE = pathlib.Path("data/simulations/stock_dev.json")

CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
ALNUM = re.compile(r"[A-Za-z0-9]{4,}")
QUESTION = re.compile(r"[?？]")
# A delegation hands the decision back to the agent and carries no value, so it
# cannot become a slot no matter how good the question was. Measured for both
# arms, this separates "the question was bad" from "there was nothing to get".
DELEGATION = re.compile(r"随便|你看着办|都行|都可以|无所谓|你决定|随你|看你")


def _tokens(text):
    return set(CJK_RUN.findall(text)) | set(ALNUM.findall(text))


def _args_blob(msg):
    parts = []
    for call in msg.get("tool_calls") or []:
        args = call.get("arguments")
        if isinstance(args, str):
            parts.append(args)
        elif isinstance(args, dict):
            parts.append(json.dumps(args, ensure_ascii=False))
    return " ".join(parts)


def analyse(path, trial=None):
    data = json.loads(path.read_text(encoding="utf-8"))

    # A subtask trajectory carries the accumulated conversation, so "the next
    # user message" after a question can be the *next subtask's instruction*
    # rather than an answer -- the first cut of this script scored exactly that
    # by mistake (it reported the P1 idx1 instruction as an answer inside
    # idx2). Instructions recur across the trajectories that hold them as
    # history; genuine answers do not. So a user message seen in two or more
    # trajectories is treated as an instruction and never as an answer.
    seen = {}
    for sim in data.get("simulations") or []:
        if trial is not None and (sim.get("trial", 0) or 0) != trial:
            continue
        for tr in (sim.get("states") or {}).get("integrity_subtask_trajectories") or []:
            here = set()
            for msg in tr.get("messages") or []:
                if msg.get("role") == "user":
                    here.add(str(msg.get("content") or "").strip())
            for content in here:
                seen[content] = seen.get(content, 0) + 1
    instructions = {c for c, n in seen.items() if n >= 2}

    q_turns = 0
    q_utilised = 0
    subtasks = 0
    subtasks_with_q = 0
    greetings = 0
    details = []
    answers = []
    for sim in data.get("simulations") or []:
        if trial is not None and (sim.get("trial", 0) or 0) != trial:
            continue
        for tr in (sim.get("states") or {}).get("integrity_subtask_trajectories") or []:
            subtasks += 1
            msgs = tr.get("messages") or []
            this_q = 0
            # Every subtask opens with the agent greeting "你好，请问需要什么服务？",
            # which is a question mark with no tool call and whose "answer" is the
            # instruction itself. Counting it made 343 of the question turns
            # greetings and pinned the utilisation rate near zero. A question only
            # counts once the user has actually stated the task.
            user_has_spoken = False
            for i, msg in enumerate(msgs):
                if msg.get("role") == "user":
                    user_has_spoken = True
                    continue
                if msg.get("role") != "assistant":
                    continue
                content = str(msg.get("content") or "")
                if not QUESTION.search(content) or msg.get("tool_calls"):
                    continue
                if not user_has_spoken:
                    greetings += 1
                    continue
                this_q += 1
                q_turns += 1
                answer = ""
                for later in msgs[i + 1 :]:
                    if later.get("role") != "user":
                        continue
                    cand = str(later.get("content") or "").strip()
                    if cand in instructions:
                        break  # the next subtask's instruction, not an answer
                    answer = cand
                    break
                if not answer:
                    continue
                answer_tokens = {t for t in _tokens(answer) if len(t) >= 2}
                if not answer_tokens:
                    continue
                used = False
                for later in msgs[i + 1 :]:
                    if later.get("role") != "assistant":
                        continue
                    blob = _args_blob(later)
                    if not blob:
                        continue
                    if any(tok in blob for tok in answer_tokens):
                        used = True
                        break
                if used:
                    q_utilised += 1
                    details.append((tr.get("subtask_id"), answer[:24]))
                answers.append(answer)
            if this_q:
                subtasks_with_q += 1
    delegations = sum(1 for a in answers if DELEGATION.search(a))
    return {
        "subtasks": subtasks,
        "q_turns": q_turns,
        "q_utilised": q_utilised,
        "answers": len(answers),
        "delegations": delegations,
        "informative": (len(answers) - delegations) / len(answers) if answers else None,
        "ask_rate": subtasks_with_q / subtasks if subtasks else 0.0,
        "questions_per_subtask": q_turns / subtasks if subtasks else 0.0,
        "hit": q_utilised / q_turns if q_turns else None,
        "greetings": greetings,
        "details": details,
    }


def main() -> None:
    arm = analyse(ARM)
    base0 = analyse(BASE, trial=0)
    base = analyse(BASE)

    print("=" * 78)
    print("提问指标（同一文本级定义，两臂都算）")
    print("=" * 78)
    hdr = f"{'':26} {'臂 (1试次)':>12} {'基线 t0':>10} {'基线 全4试次':>14}"
    print(hdr)
    def row(name, a, b, c, fmt="{:>12} {:>10} {:>14}"):
        print(f"{name:26}" + fmt.format(a, b, c))
    row("子任务数", arm["subtasks"], base0["subtasks"], base["subtasks"], "{:>12} {:>10} {:>14}")
    row("提问轮总数", arm["q_turns"], base0["q_turns"], base["q_turns"], "{:>12} {:>10} {:>14}")
    row("提问轮/子任务", f"{arm['questions_per_subtask']:.2f}",
        f"{base0['questions_per_subtask']:.2f}", f"{base['questions_per_subtask']:.2f}")
    row("会提问的子任务占比", f"{100*arm['ask_rate']:.1f}%",
        f"{100*base0['ask_rate']:.1f}%", f"{100*base['ask_rate']:.1f}%")
    row("提问命中率（回答被用于调用）",
        f"{100*arm['hit']:.1f}%" if arm["hit"] is not None else "n/a",
        f"{100*base0['hit']:.1f}%" if base0["hit"] is not None else "n/a",
        f"{100*base['hit']:.1f}%" if base["hit"] is not None else "n/a")
    row("提问无效命中（回答未被使用）",
        f"{100*(1-arm['hit']):.1f}%" if arm["hit"] is not None else "n/a",
        f"{100*(1-base0['hit']):.1f}%" if base0["hit"] is not None else "n/a",
        f"{100*(1-base['hit']):.1f}%" if base["hit"] is not None else "n/a")
    row("回答非委托率（回答里确实带信息）",
        f"{100*arm['informative']:.1f}%" if arm["informative"] is not None else "n/a",
        f"{100*base0['informative']:.1f}%" if base0["informative"] is not None else "n/a",
        f"{100*base['informative']:.1f}%" if base["informative"] is not None else "n/a")
    row("用户委托回答数（随便/你看着办…）",
        arm["delegations"], base0["delegations"], base["delegations"],
        "{:>12} {:>10} {:>14}")
    row("开头问候轮（已排除，不计入）",
        arm["greetings"], base0["greetings"], base["greetings"],
        "{:>12} {:>10} {:>14}")

    raw = json.loads(ARM.read_text(encoding="utf-8"))
    q = r = 0
    for sim in raw.get("simulations") or []:
        ev = (sim.get("states") or {}).get("adapt_agent") or {}
        q += ev.get("questions_committed", 0)
        r += ev.get("answers_resolved_to_a_value", 0)
    print()
    print(f"臂自有计数器（更严的定义，基线无对应）: 提交 {q} / 落成槽值 {r} = "
          f"{100*r/q:.1f}%" if q else "臂自有计数器: 无")
    print()
    print("两处定义的区别:")
    print("  文本级『命中』= 回答里的词出现在之后的工具调用参数里（能到调用即算）")
    print("  引擎级『落值』= 主动提问引擎把这个回答解析成了槽值（更严，仅臂有）")
    print()
    print("臂上『回答被使用』的样本:")
    for sid, ans in arm["details"][:6]:
        print(f"    {sid}: 用户答『{ans}』")


if __name__ == "__main__":
    main()
