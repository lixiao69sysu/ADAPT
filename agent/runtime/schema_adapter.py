"""ADAPT-side compatibility for tools without native x-adapt annotations.

Pristine VitaBench schemas remain untouched.  This adapter derives only
structural source roles from observable argument names and JSON schema shapes;
the open-world planner still decides entities from live result topology.
"""

from __future__ import annotations

from typing import Any


class ObservableSchemaAdapter:
    """Enrich a copied argument schema with conservative source roles."""

    @staticmethod
    def adapt(
        argument_schemas: dict[str, dict[str, Any]],
        id_arguments: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        adapted: dict[str, dict[str, Any]] = {}
        for name, raw_schema in argument_schemas.items():
            schema = dict(raw_schema)
            if not schema.get("x-adapt-source"):
                entity_type = id_arguments.get(name, "")
                if schema.get("x-adapt-question"):
                    source = "user_required"
                elif entity_type == "user":
                    source = "profile_resolvable"
                elif entity_type in {"order", "booking", "reservation", "payment"}:
                    source = "result_bindable"
                elif entity_type:
                    source = "candidate_discoverable"
                else:
                    # Non-ID action values are bound from the current task,
                    # profile, or proposal. They are never made into a hard
                    # question unless the schema explicitly supplies one.
                    source = "result_bindable"
                schema["x-adapt-source"] = source
            adapted[name] = schema
        return adapted
