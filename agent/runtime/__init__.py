"""Shared decision-side runtime helpers.

This package used to re-export the controller's state machine, tool registry,
question gate, runtime-policy store and tool-failure ledger. All of those were
retired with the controller (see docs/AGENT_ARCHITECTURE.md, section 9), so the
package is now just a namespace for the four modules the shared decision layer
actually depends on:

``alignment`` (memory/grounding imports it), ``ranking``, ``location`` and
``schedule`` (decision.py calls them). Keep it free of re-exports: an eager
``__init__`` is what once pulled the whole controller into the data layer's
import closure.
"""
