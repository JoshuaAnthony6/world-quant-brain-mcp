from __future__ import annotations

import json
from pathlib import Path
from typing import Any


POLICY_PATH = Path(__file__).resolve().parents[1] / "reference" / "policy" / "python_alpha_policy.json"


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    policy_path = Path(path) if path else POLICY_PATH
    return json.loads(policy_path.read_text())


def is_auxiliary_group_key_field(field_id: str, description: str = "", field_type: str | None = None) -> bool:
    normalized_id = field_id.lower().replace("-", "_")
    id_tokens = normalized_id.split("_")
    group_terms = {
        "market",
        "sector",
        "industry",
        "subindustry",
        "country",
        "exchange",
        "currency",
        "cluster",
        "group",
        "grouping",
        "gics",
        "sic",
    }
    if normalized_id in group_terms:
        return True
    if any(
        normalized_id.endswith(f"_{term}") or normalized_id.startswith(f"{term}_")
        for term in group_terms
    ):
        return True

    lowered_desc = description.lower()
    desc_group_markers = (
        "group key",
        "grouping",
        "classification",
        "categorical",
        "categorical label",
        "categorical cluster",
        "cluster id",
        "cluster label",
        "industry cluster",
        "sector code",
        "industry code",
        "country code",
        "exchange code",
        "currency code",
        "gics",
        "sic",
    )
    if any(marker in lowered_desc for marker in desc_group_markers):
        return True

    return str(field_type or "").upper() == "GROUP" and any(token in id_tokens for token in group_terms)


def field_role(field: dict[str, Any]) -> str:
    field_id = str(field.get("id", ""))
    field_type = str(field.get("type") or "MATRIX").upper()
    description = str(field.get("description", ""))
    if field_type == "VECTOR":
        return "forbidden_vector"
    if is_auxiliary_group_key_field(field_id, description, field_type):
        if field_type in {"MATRIX", "GROUP"}:
            return "auxiliary_group"
        return "forbidden_group_key_type"
    if field_type == "MATRIX":
        return "economic_matrix"
    if field_type == "GROUP":
        return "forbidden_group_as_economic"
    return "forbidden_unknown_type"

