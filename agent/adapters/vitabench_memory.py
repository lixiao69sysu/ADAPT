"""VitaBench adapter: adds the harness-facing surface to ``ADAPTMemory``.

``agent.memory.adapt_memory.ADAPTMemory`` is deliberately framework-free -- it
imports nothing from ``vita``, so a deployment that only wants the preference
store can use it as-is. The evaluation harness needs two extra things, and this
module is the *only* place that knows about them:

1. **The ``BaseMemory`` interface.** The vendored orchestrator calls
   ``memory.read(query=...)`` and ``memory.update(new_interactions=, llm=,
   llm_args=)``. Those methods already exist on ``ADAPTMemory``; inheriting
   ``BaseMemory`` is what satisfies the harness's own type expectations and what
   installs ``ToolKitBase.__init__(db=None)``, which the ``@is_tool`` registry
   lives on.

2. **``@is_tool`` auto-discovery.** This is the load-bearing half. The two READ
   tools and the WRITE tool below are what ``_inject_memory_tools`` puts into
   the domain toolkit, i.e. what lets the model query and write memory
   mid-conversation. Dropping them is the E-042 regression: stock called
   ``query_preference_memory`` **30 times** in one cohort while the
   hidden-tool arm called it **0**.

**The bodies and docstrings below are byte-identical to the core methods.** That
is not stylistic: ``ToolKitBase`` builds each tool's description from the
function's docstring, so a reworded docstring silently changes the text the
model sees. The adapter re-declares the methods only to attach the decorator,
then delegates to the core implementation.

MRO note: ``VitaBenchADAPTMemory`` linearises to
``[VitaBenchADAPTMemory, ADAPTMemory, BaseMemory, ToolKitBase, ...]``, so
``ADAPTMemory.__init__``'s bare ``super().__init__()`` continues into
``BaseMemory`` here and terminates at ``object`` when the class is used
standalone. That is why the core calls ``super()`` with no arguments.
"""

from __future__ import annotations

from vita.environment.toolkit import ToolType, is_tool
from vita.memory.base import BaseMemory

from agent.memory.adapt_memory import ADAPTMemory


class VitaBenchADAPTMemory(ADAPTMemory, BaseMemory):
    """``ADAPTMemory`` + the tool surface and base interface the harness needs."""

    @is_tool(ToolType.READ)
    def suggest_question_tool(self, instruction: str) -> str:
        """当用户的需求信息不完整时，返回一个需要向用户确认的问题。

        Agent 应在执行任务前调用此工具。若返回问题，先向用户询问获取答案，
        再根据答案继续执行。若无信息缺口，返回空字符串。
        """
        return ADAPTMemory.suggest_question_tool(self, instruction)

    @is_tool(ToolType.READ)
    def query_preference_memory(self, query: str) -> str:
        """根据具体问题查询用户偏好记忆，返回与该问题相关的偏好条目。

        在挑选候选、生成搜索关键词或说明推荐理由之前调用，
        可以得到该用户在此情境下的偏好证据，而不是仅依赖固定卡片。

        Args:
            query: 当前情境或需求，例如"垃圾桶 家居用品 购买偏好"。
        """
        return ADAPTMemory.query_preference_memory(self, query)

    @is_tool(ToolType.READ)
    def read_preference_memory(self) -> str:
        """读取用户偏好记忆的整体视图（包含任务相关的偏好与待确认问题）。"""
        return ADAPTMemory.read_preference_memory(self)

    @is_tool(ToolType.WRITE)
    def record_preference_answer(self, answer: str, question: str = "") -> str:
        """记录用户对主动询问的回答，供该用户后续子任务使用。

        Args:
            answer: 用户刚刚给出的答案。
            question: 对应问题；留空时使用最近一次已提交的问题。
        """
        return ADAPTMemory.record_preference_answer(self, answer, question)
