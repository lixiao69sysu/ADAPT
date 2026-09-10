from agent.intent import COMPLETION_INTENT_RE, TRANSACTION_MARKERS
for s in ["这个订单可以取消吗","帮我查一下订单状态","帮我看下订单还有多久到","帮我下订单"]:
    hit = [m.group(0) for m in COMPLETION_INTENT_RE.finditer(s)]
    marks = [m for m in TRANSACTION_MARKERS if m in s]
    print(repr(s), "regex=", hit, "markers=", marks)
