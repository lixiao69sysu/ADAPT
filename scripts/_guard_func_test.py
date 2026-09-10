import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent.vitabench_bootstrap as bootstrap
bootstrap.enable_vitabench_utf8()

from agent.vitabench_runner import make_agent_context_guard, _agent_overflow_numbers, _concat_message_content

MSG = ("This model's maximum context length is 131072 tokens. However, you "
       "requested 1024 output tokens and your prompt contains at least 130049 "
       "input tokens, for a total of at least 131073 tokens.")


class FakeOverflow(Exception):
    def __init__(self):
        super().__init__(f"Error code: 400 - {{'error': {{'message': {json.dumps(MSG)}}}}}")
        self.response = None


def test_parse():
    exc = FakeOverflow()
    parsed = _agent_overflow_numbers(exc)
    assert parsed == (131072, 1024, 130049), parsed
    print('parse OK:', parsed)


def test_guard_trims_and_recovers():
    calls = {'n': 0}

    def original_generate(*args, **kwargs):
        calls['n'] += 1
        msgs = kwargs.get("messages") or (args[1] if len(args) > 1 else None)
        text = _concat_message_content(msgs)
        if len(text) > 20000:  # simulate context-too-long based on size
            raise FakeOverflow()
        return f"accepted:{calls['n']}"

    guard = make_agent_context_guard(original_generate, "qwen38-agent")
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "tool", "content": "HUGETOOL" + "x" * 30000},
        {"role": "tool", "content": "tool2" + "y" * 5000},
        {"role": "user", "content": "hello"},
    ]
    result = guard(model="qwen38-agent", messages=msgs, max_tokens=1024)
    print('guard result:', result, '| calls:', calls['n'])
    assert str(result).startswith("accepted"), result


def test_guard_passthrough_other_model():
    def original_generate(*args, **kwargs):
        return "other"
    guard = make_agent_context_guard(original_generate, "qwen38-agent")
    assert guard(model="qwen35-user", messages=[]) == "other"
    print('passthrough OK')


if __name__ == "__main__":
    test_parse()
    test_guard_trims_and_recovers()
    test_guard_passthrough_other_model()
    print('ALL GUARD TESTS PASSED')
