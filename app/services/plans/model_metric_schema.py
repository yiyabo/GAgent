from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TypeAlias, cast


MetricDict: TypeAlias = dict[str, object]

REQUIRED_MODEL_METRICS: tuple[str, str] = ("accuracy", "macro_f1")

_MODEL_CONTAINER_KEYS = (
    "models",
    "model_metrics",
    "metrics",
    "results",
    "test_metrics",
)
_NESTED_MODEL_CONTAINER_PATHS = (
    ("overall_summary", "models"),
    ("summary", "models"),
)
_SPLIT_KEYS = ("test", "validation", "val", "holdout", "eval")
_NON_MODEL_ENTRY_NAMES = {
    "accuracy",
    "macro_f1",
    "weighted_f1",
    "f1",
    "precision",
    "recall",
    "metadata",
    "models",
    "model_metrics",
    "metrics",
    "results",
    "test_metrics",
    "overall_summary",
    "per_genus_comparison",
    "summary",
    "config",
    "configuration",
    "parameters",
    "hyperparameters",
}
_TREE_MODEL_TOKENS = (
    "randomforest",
    "extratrees",
    "xgboost",
    "lightgbm",
    "catboost",
    "gradientboosting",
    "gradientboosted",
    "gbm",
)


def collect_metric_model_entries(payload: object) -> dict[str, MetricDict]:
    entries: dict[str, MetricDict] = {}
    root = _as_string_mapping(payload)
    if root is None:
        return entries

    for key in _MODEL_CONTAINER_KEYS:
        _add_container_entries(entries, root.get(key))

    for path in _NESTED_MODEL_CONTAINER_PATHS:
        container: object | None = root
        for segment in path:
            mapping = _as_string_mapping(container)
            if mapping is None:
                container = None
                break
            container = mapping.get(segment)
        _add_container_entries(entries, container)

    for model_name, metrics in root.items():
        _add_metric_entry(entries, model_name, metrics, explicit_container=False)

    return entries


def select_model_metric_entry(metrics: Mapping[str, object]) -> MetricDict:
    direct = dict(metrics)
    if _has_all_required_metrics(direct):
        return direct

    for split_name in _SPLIT_KEYS:
        split_metrics = _as_string_mapping(metrics.get(split_name))
        if split_metrics is not None and _has_all_required_metrics(split_metrics):
            return split_metrics

    if _has_any_required_metric(direct):
        return direct


    for split_name in _SPLIT_KEYS:
        split_metrics = _as_string_mapping(metrics.get(split_name))
        if split_metrics is not None and _has_any_required_metric(split_metrics):
            return split_metrics

    return direct


def has_required_model_metrics(metrics: Mapping[str, object]) -> bool:
    return all(is_finite_number(metrics.get(field)) for field in REQUIRED_MODEL_METRICS)


def missing_required_model_metrics(metrics: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(
        field
        for field in REQUIRED_MODEL_METRICS
        if not is_finite_number(metrics.get(field))
    )


def is_tree_model_entry(name: str, metrics: Mapping[str, object]) -> bool:
    candidates = [name]
    for key in ("type", "model_type", "estimator", "algorithm", "family"):
        value = metrics.get(key)
        if isinstance(value, str):
            candidates.append(value)
    return any(_contains_tree_model_token(candidate) for candidate in candidates)


def is_finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float, str)):
        return False
    try:
        number = float(value)
    except ValueError:
        return False
    return number == number and number not in {float("inf"), float("-inf")}


def _add_container_entries(entries: dict[str, MetricDict], container: object) -> None:
    mapping = _as_string_mapping(container)
    if mapping is not None:
        for model_name, metrics in mapping.items():
            _add_metric_entry(entries, model_name, metrics, explicit_container=True)
    elif isinstance(container, list):
        for item in cast(list[object], container):
            item_mapping = _as_string_mapping(item)
            if item_mapping is None:
                continue
            name = item_mapping.get("model") or item_mapping.get("name") or item_mapping.get("estimator")
            _add_metric_entry(entries, name, item_mapping, explicit_container=True)


def _add_metric_entry(
    entries: dict[str, MetricDict],
    name: object,
    metrics: object,
    *,
    explicit_container: bool,
) -> None:
    metric_mapping = _as_string_mapping(metrics)
    if not isinstance(name, str) or not name.strip() or metric_mapping is None:
        return
    normalized_name = name.strip()
    if _is_non_model_entry_name(normalized_name):
        return
    selected = select_model_metric_entry(metric_mapping)
    if not explicit_container and not (_has_any_required_metric(selected) or is_tree_model_entry(normalized_name, selected)):
        return
    entries[normalized_name] = selected


def _has_all_required_metrics(metrics: Mapping[str, object]) -> bool:
    return all(is_finite_number(metrics.get(field)) for field in REQUIRED_MODEL_METRICS)


def _has_any_required_metric(metrics: Mapping[str, object]) -> bool:
    return any(is_finite_number(metrics.get(field)) for field in REQUIRED_MODEL_METRICS)


def _is_non_model_entry_name(name: str) -> bool:
    return name.strip().lower() in _NON_MODEL_ENTRY_NAMES


def _contains_tree_model_token(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", value.lower())
    return any(token in normalized for token in _TREE_MODEL_TOKENS)


def _as_string_mapping(value: object) -> MetricDict | None:
    if not isinstance(value, dict):
        return None
    raw = cast(dict[object, object], value)
    return {key: item for key, item in raw.items() if isinstance(key, str)}
