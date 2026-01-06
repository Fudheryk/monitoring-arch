# server/app/domain/policies.py

from __future__ import annotations
"""
Règles métier utilisées pour évaluer les seuils.

Fonction principale :
    match_condition(metric_type, condition, sample_value, threshold_or_value)
Compare une valeur mesurée à un seuil en fonction du type.
"""

import operator as op
import re
from datetime import datetime
from typing import Any

OPS = {
    "gt": op.gt,  ">":  op.gt,
    "ge": op.ge,  ">=": op.ge,
    "lt": op.lt,  "<":  op.lt,
    "le": op.le,  "<=": op.le,
    "eq": op.eq,  "==": op.eq,
    "ne": op.ne,  "!=": op.ne,
}

def _norm_metric_type(t: str | None) -> str:
    """Normalise le type de métrique en 'number', 'boolean' ou 'string'."""
    if not t:
        return "string"
    t = t.strip().lower()
    if t in {"numeric", "number", "percent", "integer", "float"}:
        return "number"
    if t in {"bool", "boolean"}:
        return "boolean"
    return "string"

def _rhs_from_threshold_or_value(norm_type: str, rhs: Any) -> Any:
    """Extrait la valeur seuil depuis un objet, dict ou valeur brute."""
    if hasattr(rhs, "value_num") or hasattr(rhs, "value_bool") or hasattr(rhs, "value_str"):
        if norm_type == "number":
            return getattr(rhs, "value_num", None)
        if norm_type == "boolean":
            return getattr(rhs, "value_bool", None)
        return getattr(rhs, "value_str", None)
    if isinstance(rhs, dict):
        if norm_type == "number":
            return rhs.get("value_num")
        if norm_type == "boolean":
            return rhs.get("value_bool")
        return rhs.get("value_str")
    return rhs

def match_condition(metric_type: str, condition: str, sample_value: Any, threshold_or_value: Any) -> bool:
    """
    Compare sample_value au seuil selon metric_type et condition.
    - number : {gt,ge,lt,le,eq,ne}
    - bool   : {eq,ne}
    - string : {eq,ne,contains,not_contains,regex}
    """
    mtype = _norm_metric_type(metric_type)
    cond = (condition or "").strip().lower()

    if mtype == "number":
        try:
            left = float(sample_value)
            right = float(_rhs_from_threshold_or_value(mtype, threshold_or_value))
        except (TypeError, ValueError):
            return False
        fn = OPS.get(cond)
        return bool(fn(left, right)) if fn else False

    if mtype == "boolean":
        try:
            left = bool(sample_value)
            right = bool(_rhs_from_threshold_or_value(mtype, threshold_or_value))
        except Exception:
            return False
        fn = OPS.get(cond)
        return bool(fn(left, right)) if fn else False

    # string
    left = "" if sample_value is None else str(sample_value)
    right_raw = _rhs_from_threshold_or_value("string", threshold_or_value)
    right = None if right_raw is None else str(right_raw)
    if cond == "contains":
        return (right is not None) and (right in left)
    if cond == "not_contains":
        return (right is not None) and (right not in left)
    if cond == "regex":
        if right is None:
            return False
        try:
            return re.search(right, left) is not None
        except re.error:
            return False
    fn = OPS.get(cond)
    if fn is None or right is None:
        return False
    return bool(fn(left, right))

def apply_min_duration(previous_severity: str, since: datetime, now: datetime,
                       desired: str, min_duration_s: int) -> str:
    """Anti-flapping simple : conserve l’état précédent si la durée mini n’est pas atteinte."""
    if desired == previous_severity:
        return desired
    elapsed = (now - since).total_seconds()
    return previous_severity if elapsed < max(0, min_duration_s) else desired
