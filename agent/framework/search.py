"""Search-result trimming for the ADAPT framework.

The stock VitaBench search tools return the top-100 fuzzy matches with no
relevance floor. Most of those are junk, and together they blow up the agent
context (the 27-38K-char tool messages that killed v17 trials at 131K tokens).
This module caps the result list and drops near-irrelevant hits.
"""

from agent.framework.config import SEARCH_MIN_SCORE, SEARCH_TOP_K


def rank_and_limit(keywords, tag_dict, top_k=None, min_score=None):
    """Return ``[(id, display)]`` ranked by relevance, capped and floored.

    Falls back to the single best match if nothing clears the floor, so the
    agent never sees an empty result for a plausible keyword.
    """
    from vita.utils.utils import rerank

    top_k = top_k if top_k is not None else SEARCH_TOP_K
    min_score = min_score if min_score is not None else SEARCH_MIN_SCORE
    if isinstance(keywords, (list, tuple)):
        keywords = "".join(keywords)
    scored = rerank(keywords, tag_dict, with_score=True)  # [(id, display, score)]
    hits = [(i, d, s) for i, d, s in scored if s >= min_score]
    if not hits:
        hits = scored[:1]
    return [(i, d) for i, d, _ in hits[:top_k]]


class SearchLoopGuard:
    """Per-toolkit state to stop the agent re-issuing searches that return the
    same result set.

    Toolkit instances are rebuilt per subtask, so the counter naturally resets
    between subtasks. The guard keys on the OUTPUT identity rather than the raw
    query, because the agent frequently shuffles keyword order / drops a word
    while re-searching the same thing — an exact-query dedup never fires on
    that. When the same result set appears a few times it appends a hint that
    the returned results are the complete candidate set, so the agent should
    pick the closest match or report it cannot fulfill, not keep searching.
    """

    def __init__(self, repeat_limit: int = 3):
        self._seen = {}
        self._repeat_limit = repeat_limit

    def note(self, output: str) -> str:
        key = (output or "").strip()
        self._seen[key] = self._seen.get(key, 0) + 1
        if self._seen[key] >= self._repeat_limit:
            return (
                "\n[ADAPT] 提示：此搜索已重复多次且结果未变，以上返回结果就是当前环境可用候选的全部。"
                "若目标商品/店铺不在结果中，说明当前环境不存在该商品；请直接选择结果中最接近的商品/服务下单，"
                "或明确告知用户无法满足，不要再重复相同的搜索。"
            )
        return ""
