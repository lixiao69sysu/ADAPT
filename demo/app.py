"""ADAPT 偏好与主动询问演示台 —— 类美团界面。

设计目标（与评测无关）
----------------------
这是一个**展示界面**，不是评测工具。它要让人一眼看到三件事：

1. **偏好被结构化地记住了**：不是一段文本，是带作用域、极性、置信度、证据的
   事实；单值槽会取代，多值集合会累加。
2. **主动询问有理由**：每一次提问都能说出"问的是哪个槽、为什么现在问"。
3. **回答会进事实库并改变后续**：用户答完，卡片跟着变。

为什么零模型也能跑
------------------
`ADAPTMemory`、`ProactiveEngine`、`DecisionCard` 全是确定性纯 Python：
偏好抽取（启发式路径）、槽解析、卡片渲染、缺口检测都不需要 LLM。
所以这个 demo **不需要任何模型端点**，`python demo/app.py` 就能起来。
（LLM 抽取与画像摘要是可选增强，本版不启用。）

界面
----
左栏 = 类美团对话（用户说话 / 助手回话 / 询问卡片）
右栏 = Agent 视角，五个折叠面板：
    记忆事实   每条：槽 / 值 / 极性 / 置信 / 状态 / 证据
    决策卡     必须 / 避免 / 偏好 / 待问 / 证据（**就是注入模型的那张卡**）
    槽状态     已定 / 待定，以及待定是否"可问"
    注入块     逐字预览真正拼进 system prompt 的记忆文本
    主动询问   提议的问题 + 它来自哪个槽

用法
----
    python demo/app.py                 # http://127.0.0.1:8770
    python demo/app.py --port 9000
    python demo/app.py --task U200109  # 直接指定用户

不依赖第三方库；只用标准库 + 仓库自身的模块。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.adapt_agent import task_state_slots  # noqa: E402
from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402

# ---------------------------------------------------------------------------
# 会话状态：一次只服务一个"正在演示的用户"，够用且简单
# ---------------------------------------------------------------------------


class Session:
    """One演示会话：一个用户 + 一条已重放的记忆 + 一面对话记录。"""

    def __init__(self, task: Any) -> None:
        self.task = task
        self.chat: list[dict[str, str]] = []
        self.index = 0
        self.memory = ADAPTMemory(language="chinese", user_id=str(task.id))
        self.current_instruction = ""

    def advance(self) -> str:
        """进入下一个子任务。返回它的指令（这就是"用户这轮说的话"）。"""
        if self.index >= len(self.task.subtasks):
            return ""
        subtask = self.task.subtasks[self.index]
        instruction = subtask.instruction or ""
        # L1 order: this subtask's own history is folded in (2a) *before* the
        # subtask runs, then the instruction is set (2b).
        self.memory.update(list(getattr(subtask, "interactions", None) or []))
        self.memory.begin_subtask(instruction)
        self.current_instruction = instruction
        self.index += 1
        return instruction

    def jump(self, index: int) -> str:
        """回到第 index 个子任务重放（用于跳到"会主动提问"的地方）。"""
        self.memory.reset()
        self.chat = []
        self.index = 0
        for _ in range(max(0, index)):
            if not self.advance():
                return ""
        return self.advance()

    # -- 视图 ---------------------------------------------------------------
    def facts_view(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for fact in self.memory.facts:
            rows.append(
                {
                    "slot": " · ".join(
                        [
                            fact.scope,
                            fact.facet,
                            fact.dimension,
                            fact.category,
                        ]
                    ),
                    "value": fact.value,
                    "polarity": fact.polarity,
                    "confidence": round(float(fact.confidence), 3),
                    "status": fact.status,
                    "evidence": len(getattr(fact, "evidence_ids", []) or []),
                    "observed_at": fact.observed_at,
                }
            )
        rows.sort(key=lambda r: (r["status"] != "active", -r["confidence"]))
        return rows

    def card_view(self) -> dict[str, Any]:
        if not self.current_instruction:
            return {}
        card = self.memory.compile_task(self.current_instruction)
        question = self.memory.propose_question(self.current_instruction)
        if question:
            card.ask.insert(0, question)
        return {
            "must": list(card.must),
            "avoid": list(card.avoid),
            "prefer": list(dict.fromkeys(card.prefer))[:12],
            "prefer_total": len(set(card.prefer)),
            "ask": list(card.ask),
            "rendered": card.render(),
        }

    def slots_view(self) -> list[dict[str, Any]]:
        if not self.current_instruction:
            return []
        resolved = dict(self.memory.resolve_task_slots(self.current_instruction))
        out = []
        for slot, settled in task_state_slots(self.current_instruction, resolved):
            out.append(
                {
                    "slot": slot,
                    "settled": bool(settled),
                    "value": resolved.get(slot, ""),
                }
            )
        return out

    def injected_block(self) -> str:
        if not self.current_instruction:
            return ""
        return self.memory.read(query=self.current_instruction)

    def current_question(self, commit: bool = False) -> str | None:
        """当前子任务要问的问题。

        ``commit=True`` 时同时花掉提问预算——**这是回答能落成事实的前提**：
        ``record_user_answer`` 只把答复挂到"已提交的问题"上，而
        ``propose_question`` 本身是不花预算的提案。真实链路里由 ADAPT Agent 在
        观察到模型确实问了之后调用 commit，这里由界面代替它。
        """
        if not self.current_instruction:
            return None
        question = self.memory.propose_question(self.current_instruction)
        if question and commit:
            self.memory.commit_question(question)
        return question

    def state(self) -> dict[str, Any]:
        return {
            "user": str(self.task.id),
            "index": self.index,
            "total": len(self.task.subtasks),
            "chat": self.chat,
            "facts": self.facts_view(),
            "card": self.card_view(),
            "slots": self.slots_view(),
            "injected": self.injected_block(),
        }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_TASKS: dict[str, Any] = {}
_SESSION: Session | None = None
_LOCK = threading.Lock()


def _tasks() -> dict[str, Any]:
    if not _TASKS:
        from agent.vitabench_runner import get_tasks

        for task in get_tasks("chinese"):
            _TASKS[str(task.id)] = task
    return _TASKS


def scan_questions(task: Any) -> list[dict[str, Any]]:
    """该用户哪些子任务会触发主动提问（独立重放，不打扰当前会话）。"""
    memory = ADAPTMemory(language="chinese", user_id=str(task.id))
    out: list[dict[str, Any]] = []
    for index, subtask in enumerate(task.subtasks):
        instruction = subtask.instruction or ""
        memory.update(list(getattr(subtask, "interactions", None) or []))
        memory.begin_subtask(instruction)
        question = memory.propose_question(instruction)
        if question:
            out.append(
                {"index": index, "instruction": instruction, "question": question}
            )
    return out


def _session() -> Session:
    global _SESSION
    if _SESSION is None:
        first = sorted(_tasks())[0]
        _SESSION = Session(_tasks()[first])
    return _SESSION


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # 静音
        return

    def _send(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/state"):
            with _LOCK:
                self._send(_session().state())
            return
        if self.path.startswith("/api/users"):
            self._send(sorted(_tasks()))
            return
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        global _SESSION
        payload = self._read()
        with _LOCK:
            session = _session()
            if self.path.startswith("/api/load"):
                task = _tasks().get(str(payload.get("task_id")))
                if task is None:
                    self._send({"error": "unknown task"}, 404)
                    return
                _SESSION = session = Session(task)
                instruction = session.advance()
                session.chat = [{"role": "user", "text": instruction}]
                self._send(
                    {**session.state(), "scan": scan_questions(task)}
                )
                return
            elif self.path.startswith("/api/jump"):
                index = int(payload.get("index") or 0)
                instruction = session.jump(index)
                if not instruction:
                    self._send({"error": "bad index"}, 400)
                    return
                session.chat = [{"role": "user", "text": instruction}]
            elif self.path.startswith("/api/next"):
                instruction = session.advance()
                if not instruction:
                    self._send({"error": "no more subtasks"}, 400)
                    return
                session.chat.append({"role": "user", "text": instruction})
            elif self.path.startswith("/api/answer"):
                question = str(payload.get("question") or "")
                answer = str(payload.get("answer") or "")
                landed = session.memory.record_user_answer(answer, question or None)
                session.chat.append({"role": "user", "text": answer})
                session.chat.append(
                    {
                        "role": "agent",
                        "text": (
                            "已记下，并落成事实。"
                            if landed
                            else "已记下（未能匹配到已提交的问题）。"
                        ),
                    }
                )
            state = session.state()
            # commit=True: 界面代替 ADAPT Agent 完成"模型确实问了"的记账，
            # 否则 record_user_answer 没有可挂靠的已提交问题。
            state["question"] = session.current_question(commit=True)
            self._send(state)


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>ADAPT · 偏好与主动询问演示台</title>
<style>
:root{--meituan:#FFD100;--ink:#1a1a1a;--line:#e8e8e8;--dim:#8a8a8a}
*{box-sizing:border-box}body{margin:0;font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);background:#f5f6f8}
header{background:var(--meituan);padding:10px 18px;display:flex;align-items:center;gap:14px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
header b{font-size:16px}header select,header button{font:inherit;padding:5px 10px;border:1px solid rgba(0,0,0,.15);border-radius:6px;background:#fff;cursor:pointer}
header .sp{margin-left:auto;color:#5a4a00;font-size:12px}
main{display:grid;grid-template-columns:minmax(360px,1fr) minmax(420px,1.15fr);gap:14px;padding:14px;height:calc(100vh - 52px)}
.col{background:#fff;border:1px solid var(--line);border-radius:10px;display:flex;flex-direction:column;overflow:hidden}
.col>h2{margin:0;padding:10px 14px;font-size:13px;letter-spacing:.04em;color:var(--dim);border-bottom:1px solid var(--line);text-transform:uppercase}
.chat{flex:1;overflow:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
.bub{max-width:86%;padding:9px 12px;border-radius:12px;white-space:pre-wrap;word-break:break-word}
.bub.user{align-self:flex-end;background:var(--meituan)}
.bub.agent{align-self:flex-start;background:#f1f2f4}
.qcard{align-self:flex-start;border:1px solid var(--meituan);background:#fffdf0;border-radius:12px;padding:12px;max-width:92%}
.qcard h4{margin:0 0 6px;font-size:12px;color:#8a6d00;letter-spacing:.04em}
.qcard p{margin:0 0 10px}
.qcard form{display:flex;gap:8px}
.qcard input{flex:1;font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:8px}
.qcard button{font:inherit;padding:7px 14px;border:0;border-radius:8px;background:var(--meituan);cursor:pointer;font-weight:600}
.panes{flex:1;overflow:auto;padding:10px 14px}
details{border:1px solid var(--line);border-radius:8px;margin-bottom:8px}
summary{padding:8px 10px;cursor:pointer;font-weight:600;font-size:13px}
summary span{float:right;color:var(--dim);font-weight:400}
table{width:100%;border-collapse:collapse;font-size:12.5px}
td,th{padding:5px 8px;border-top:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:500}
pre{margin:0;padding:10px;background:#fbfbfc;border-top:1px solid var(--line);white-space:pre-wrap;font-size:12.5px;font-family:ui-monospace,Menlo,Consolas,monospace}
.tag{display:inline-block;padding:1px 6px;border-radius:999px;font-size:11px;border:1px solid var(--line)}
.neg{background:#ffecec;border-color:#ffc9c9;color:#a11}
.pos{background:#eef7ee;border-color:#c9e6c9;color:#161}
.sup{background:#f2f2f2;color:#999}
.mini{color:var(--dim);font-size:11.5px}
.empty{color:var(--dim);padding:8px 0}
</style></head><body>
<header>
  <b>ADAPT</b><span class="mini">偏好与主动询问演示台</span>
  <select id="users"></select>
  <button onclick="load()">载入用户</button>
  <button onclick="next()">下一个子任务 ▶</button>
  <button onclick="jumpNext()" id="jumpBtn">跳到会提问处 ⏭</button>
  <span class="sp" id="where"></span>
</header>
<main>
  <section class="col">
    <h2>对话（类美团）</h2>
    <div class="chat" id="chat"></div>
  </section>
  <section class="col">
    <h2>Agent 视角</h2>
    <div class="panes">
      <details open><summary>决策卡（注入模型的那张卡）<span id="cardN"></span></summary><div id="card"></div></details>
      <details><summary>槽状态<span id="slotN"></span></summary><div id="slots"></div></details>
      <details><summary>记忆事实<span id="factN"></span></summary><div id="facts"></div></details>
      <details><summary>主动询问<span id="askN"></span></summary><div id="ask"></div></details>
      <details><summary>注入块（逐字预览）<span id="injN"></span></summary><pre id="inj"></pre></details>
    </div>
  </section>
</main>
<script>
let S=null;
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
async function api(path,body){
  const r=await fetch(path,body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{});
  return r.json();
}
function render(){
  if(!S)return;
  document.getElementById("where").textContent=`用户 ${S.user} · 子任务 ${S.index}/${S.total}`+((S.scan||[]).length?` · 该用户有 ${S.scan.length} 处会提问`:"");
  const chat=document.getElementById("chat");
  chat.innerHTML="";
  (S.chat||[]).forEach(m=>{
    const d=document.createElement("div");
    d.className="bub "+(m.role==="user"?"user":"agent");
    d.textContent=m.text;chat.appendChild(d);
  });
  if(S.question){
    const d=document.createElement("div");
    d.className="qcard";
    d.innerHTML=`<h4>AGENT 主动询问</h4><p>${esc(S.question)}</p>
      <form onsubmit="answer(event)"><input id="ans" placeholder="以用户身份回答…" autocomplete="off"><button>发送</button></form>`;
    chat.appendChild(d);
  }
  chat.scrollTop=chat.scrollHeight;

  const c=S.card||{};
  document.getElementById("cardN").textContent=`偏好 ${c.prefer_total??0} 条`;
  const seen=new Set();
  const row=(k,v,cls)=>v&&v.length?`<tr><th>${k}</th><td>${v.map(x=>`<span class="tag ${cls||""}">${esc(x)}</span>`).join(" ")}</td></tr>`:"";
  document.getElementById("card").innerHTML=`<table>
    ${row("MUST",c.must)}${row("AVOID",c.avoid,"neg")}${row("PREFER",c.prefer,"pos")}${row("ASK",c.ask)}
  </table>`;

  const sl=S.slots||[];
  document.getElementById("slotN").textContent=sl.length?`待定 ${sl.filter(x=>!x.settled).length}`:"—";
  document.getElementById("slots").innerHTML=sl.length?`<table><tr><th>槽</th><th>状态</th><th>值</th></tr>`+
    sl.map(x=>`<tr><td>${esc(x.slot)}</td><td>${x.settled?'<span class="tag pos">已定</span>':'<span class="tag">待定</span>'}</td><td>${esc(x.value)||'<span class="mini">—</span>'}</td></tr>`).join("")+`</table>`
    :`<div class="empty">本指令未声明必需槽。</div>`;

  const f=S.facts||[];
  document.getElementById("factN").textContent=`${f.length} 条`;
  document.getElementById("facts").innerHTML=f.length?`<table><tr><th>槽</th><th>值</th><th>极</th><th>信</th><th>状态</th><th>证据</th></tr>`+
    f.map(x=>`<tr><td class="mini">${esc(x.slot)}</td><td>${esc(x.value)}</td>
      <td>${x.polarity==="negative"?'<span class="tag neg">负</span>':'<span class="tag pos">正</span>'}</td>
      <td>${x.confidence}</td><td>${x.status==="active"?'<span class="tag">活跃</span>':'<span class="tag sup">已被取代</span>'}</td>
      <td class="mini">${x.evidence}</td></tr>`).join("")+`</table>`
    :`<div class="empty">还没有事实。</div>`;

  document.getElementById("askN").textContent=S.question?"1":"0";
  document.getElementById("ask").innerHTML=S.question
    ?`<table><tr><th>提议的问题</th><td>${esc(S.question)}</td></tr>
       <tr><th>来源</th><td class="mini">ProactiveEngine 从"仅用户可答"的缺口里提出；提交后才花提问预算</td></tr></table>`
    :`<div class="empty">当前指令没有可问的缺口。</div>`;

  const inj=S.injected||"";
  document.getElementById("injN").textContent=`${inj.length} 字符`;
  document.getElementById("inj").textContent=inj;
}
async function boot(){
  const users=await api("/api/users");
  document.getElementById("users").innerHTML=users.map(u=>`<option>${u}</option>`).join("");
  S=await api("/api/state");render();
}
async function load(){
  S=await api("/api/load",{task_id:document.getElementById("users").value});
  S.question=(S.card||{}).ask?.[0]||null;render();
}
async function jumpNext(){
  const scan=S?.scan||[];
  const nxt=scan.find(x=>x.index>=S.index);
  if(!nxt){alert("该用户后面没有会触发主动提问的子任务了。");return;}
  S=await api("/api/jump",{index:nxt.index});
  S.question=(S.card||{}).ask?.[0]||null;render();
}
async function next(){
  S=await api("/api/next",{});render();
}
async function answer(e){
  e.preventDefault();
  const v=document.getElementById("ans").value.trim();if(!v)return;
  S=await api("/api/answer",{question:S.question||"",answer:v});render();
}
boot();
</script></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--task", default="")
    args = parser.parse_args()

    if args.task:
        global _SESSION
        task = _tasks().get(args.task)
        if task is None:
            print(f"unknown task {args.task}", file=sys.stderr)
            return 2
        _SESSION = Session(task)
        _SESSION.advance()
        _SESSION.chat = [{"role": "user", "text": _SESSION.current_instruction}]

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ADAPT demo → http://{args.host}:{args.port}   (Ctrl+C 停止)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
