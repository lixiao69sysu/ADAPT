import re

msg = ("This model's maximum context length is 131072 tokens. However, you "
       "requested 1024 output tokens and your prompt contains at least 130049 "
       "input tokens, for a total of at least 131073 tokens. Please reduce the "
       "length of the input prompt or the number of requested output tokens. "
       "(parameter=input_tokens, value=130049)")

rx = re.compile(
    r"maximum context length is (\d+) tokens.*?"
    r"you requested (\d+) output tokens.*?"
    r"prompt contains at least (\d+) input tokens",
    re.DOTALL,
)
m = rx.search(msg)
print('regex match:', m.groups() if m else None)

# also test the "would you like" variant that vLLM sometimes emits
msg2 = ("This model's maximum context length is 262144 tokens. However, you "
        "requested 1024 output tokens and your prompt contains at least 261121 "
        "input tokens, for a total of at least 262145 tokens.")
m2 = rx.search(msg2)
print('regex match 2:', m2.groups() if m2 else None)
