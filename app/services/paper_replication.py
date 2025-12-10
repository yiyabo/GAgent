"""Paper replication experiment metadata structures and registry.

This module defines ExperimentCard, YAML-based loading/saving, and a registry
that scans ``data/<experiment_id>/card.yaml`` instead of hardcoding cards in
code. Use ``generate_experiment_card`` (tool) to create new cards from papers.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# Project root and data directory
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_DATA_DIR = _PROJECT_ROOT / "data"
_CARD_FILENAME = "card.yaml"

# In-memory caches for loaded cards/paths
_CARD_CACHE: Dict[str, "ExperimentCard"] = {}
_CARD_PATH_CACHE: Dict[str, Path] = {}


@dataclass
class ExperimentMetric:
    """Single evaluation metric for an experiment."""

    name: str
    type: str = "scalar"
    higher_is_better: Optional[bool] = None
    paper_value: Optional[float] = None
    unit: Optional[str] = None
    tolerance: Optional[float] = None
    reported_location: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PhageInfo:
    """High-level description of the phage or phage-derived system."""

    name: Optional[str] = None
    family: Optional[str] = None
    genome_type: Optional[str] = None
    isolation_source: Optional[str] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class HostInfo:
    """Description of the bacterial host system."""

    species: Optional[str] = None
    strain: Optional[str] = None
    genotype: Optional[str] = None
    resistance_profile: List[str] = field(default_factory=list)
    growth_medium: Optional[str] = None
    growth_temperature_c: Optional[float] = None
    shaking_rpm: Optional[int] = None
    starting_od600: Optional[float] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class PhageSystem:
    """Combined description of phage and host."""

    phage: PhageInfo = field(default_factory=PhageInfo)
    host: HostInfo = field(default_factory=HostInfo)


@dataclass
class Assay:
    """Assay or experiment type and key protocol descriptors."""

    type: str
    description: str
    moi: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExperimentCard:
    """Machine-readable description of a single experiment."""

    paper: Dict[str, Any]
    experiment: Dict[str, Any]
    task: Dict[str, Any]

    phage_system: Optional[PhageSystem] = None
    assay: Optional[Assay] = None
    dataset: Dict[str, Any] = field(default_factory=dict)
    model: Dict[str, Any] = field(default_factory=dict)
    metrics: List[ExperimentMetric] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _validate_card_dict(card_dict: Dict[str, Any]) -> List[str]:
    """Validate minimal required fields for a card."""
    errors: List[str] = []

    def _require(path: Tuple[str, ...]) -> None:
        cursor: Any = card_dict
        for key in path:
            if not isinstance(cursor, dict) or key not in cursor or cursor[key] in (None, ""):
                errors.append(f"Missing required field: {'/'.join(path)}")
                return
            cursor = cursor[key]

    _require(("paper", "title"))
    _require(("experiment", "id"))
    _require(("experiment", "name"))
    _require(("task", "description"))

    return errors


def _dict_to_card(card_dict: Dict[str, Any]) -> ExperimentCard:
    """Convert a validated dict into an ExperimentCard instance."""
    metrics_raw = card_dict.get("metrics", [])
    metrics: List[ExperimentMetric] = []
    for item in metrics_raw or []:
        if not isinstance(item, dict):
            continue
        metrics.append(
            ExperimentMetric(
                name=item.get("name", ""),
                type=item.get("type", "scalar"),
                higher_is_better=item.get("higher_is_better"),
                paper_value=item.get("paper_value"),
                unit=item.get("unit"),
                tolerance=item.get("tolerance"),
                reported_location=item.get("reported_location"),
                extra=item.get("extra") or {},
            )
        )

    phage_system = None
    phage_system_raw = card_dict.get("phage_system")
    if isinstance(phage_system_raw, dict):
        phage_system = PhageSystem(
            phage=PhageInfo(**(phage_system_raw.get("phage") or {})),
            host=HostInfo(**(phage_system_raw.get("host") or {})),
        )

    assay = None
    assay_raw = card_dict.get("assay")
    if isinstance(assay_raw, dict):
        try:
            assay = Assay(
                type=assay_raw.get("type", "unspecified"),
                description=assay_raw.get("description", ""),
                moi=assay_raw.get("moi"),
                details=assay_raw.get("details") or {},
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to hydrate assay section: %s", exc)

    return ExperimentCard(
        paper=card_dict.get("paper", {}),
        experiment=card_dict.get("experiment", {}),
        task=card_dict.get("task", {}),
        phage_system=phage_system,
        assay=assay,
        dataset=card_dict.get("dataset", {}) or {},
        model=card_dict.get("model", {}) or {},
        metrics=metrics,
        artifacts=card_dict.get("artifacts", {}) or {},
        constraints=card_dict.get("constraints", {}) or {},
        notes=card_dict.get("notes", []) or [],
    )


def _load_card_yaml(path: Path) -> Dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    return yaml.safe_load(content) or {}


def discover_experiments(data_dir: Path = _DATA_DIR) -> List[str]:
    """Return experiment IDs that contain a card.yaml."""
    if not data_dir.exists():
        return []
    ids: List[str] = []
    for entry in data_dir.iterdir():
        if entry.is_dir() and (entry / _CARD_FILENAME).exists():
            ids.append(entry.name)
    return sorted(ids)


def list_experiment_cards(reload: bool = False) -> List[Dict[str, Any]]:
    """List available experiment cards with basic metadata."""
    result: List[Dict[str, Any]] = []
    for exp_id in discover_experiments():
        try:
            card = load_experiment_card(exp_id, reload=reload)
            result.append(
                {
                    "id": exp_id,
                    "title": card.paper.get("title"),
                    "path": str(_CARD_PATH_CACHE.get(exp_id, _DATA_DIR / exp_id / _CARD_FILENAME)),
                }
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load card for %s: %s", exp_id, exc)
    return result


def load_experiment_card(experiment_id: str, reload: bool = False) -> ExperimentCard:
    """Load an ExperimentCard from data/<experiment_id>/card.yaml."""
    if not experiment_id:
        raise ValueError("experiment_id is required")

    if experiment_id in _CARD_CACHE and not reload:
        return _CARD_CACHE[experiment_id]

    card_path = _DATA_DIR / experiment_id / _CARD_FILENAME
    if not card_path.exists():
        raise FileNotFoundError(f"Card not found for experiment_id '{experiment_id}'. Expected at {card_path}")
    try:
        if card_path.stat().st_size == 0:
            raise ValueError(f"Card file is empty: {card_path}")
    except OSError:
        raise FileNotFoundError(f"Unable to access card for experiment_id '{experiment_id}' at {card_path}")

    card_dict = _load_card_yaml(card_path)
    errors = _validate_card_dict(card_dict)
    if errors:
        raise ValueError(f"Card validation failed for '{experiment_id}': {errors}")

    card = _dict_to_card(card_dict)
    _CARD_CACHE[experiment_id] = card
    _CARD_PATH_CACHE[experiment_id] = card_path
    return card


def save_experiment_card(experiment_id: str, card: ExperimentCard, overwrite: bool = False) -> Path:
    """Persist an ExperimentCard to data/<experiment_id>/card.yaml."""
    if not experiment_id:
        raise ValueError("experiment_id is required")

    exp_dir = _DATA_DIR / experiment_id
    exp_dir.mkdir(parents=True, exist_ok=True)
    card_path = exp_dir / _CARD_FILENAME
    if card_path.exists() and not overwrite:
        raise FileExistsError(f"Card already exists at {card_path}. Use overwrite=True to replace.")

    with card_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(card.to_dict(), f, sort_keys=False, allow_unicode=True)

    _CARD_CACHE[experiment_id] = card
    _CARD_PATH_CACHE[experiment_id] = card_path
    return card_path
