"""Adapters that bind the framework-free core to a specific host framework.

Everything in this package is allowed to import a host framework (currently
VitaBench). Nothing in ``agent.memory`` may: the preference store is meant to
run without the evaluation harness, and
``agent/tests/test_memory_is_framework_free.py`` enforces that boundary.
"""
