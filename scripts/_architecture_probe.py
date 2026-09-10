import inspect
from agent.vitabench_bootstrap import enable_vitabench_utf8
enable_vitabench_utf8()
from agent.adapt_agent import ADAPTAgent
from vita.agent.personalization_agent import PersonalizationAgent
from vita.agent.llm_agent import LLMAgent

print("MRO:", [c.__name__ for c in ADAPTAgent.__mro__[:5]])
base = set(dir(LLMAgent)) | set(dir(PersonalizationAgent))
own = [name for name, value in vars(ADAPTAgent).items() if callable(value) and not name.startswith("__")]
overridden = [n for n in own if n in base]
added = [n for n in own if n not in base]
print("\nOverrides (%d):" % len(overridden), sorted(overridden))
print("\nADAPT-only methods (%d):" % len(added))
print(sorted(added))
