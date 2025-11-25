"""Paper replication experiment metadata structures.

This module defines ExperimentCard and helpers for describing
reproducible experiments, with a first baseline instance for
``data/experiment_1`` (BacteriophageHostPrediction).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


# Project root and data directory
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_DATA_DIR = _PROJECT_ROOT / "data"


@dataclass
class ExperimentMetric:
    """Single evaluation metric for an experiment.

    This is intentionally generic and can be used for ML metrics
    (e.g. PR-AUC) or wet-lab readouts (e.g. log10 reduction, PFU/mL).
    """

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
    """Combined description of phage and host.

    This is intentionally coarse-grained; detailed protocol-level
    parameters belong in the Assay section.
    """

    phage: PhageInfo = field(default_factory=PhageInfo)
    host: HostInfo = field(default_factory=HostInfo)


@dataclass
class Assay:
    """Assay or experiment type and key protocol descriptors.

    For wet-lab experiments this may describe MOI, incubation
    conditions, sampling schedule, etc. For in-silico experiments
    this can describe the main computational pipeline.
    """

    type: str
    description: str
    moi: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExperimentCard:
    """Machine-readable description of a single experiment.

    The card is intentionally flexible: most fields are dictionaries
    so that upstream LLMs can populate them incrementally. For
    logging or exporting, use ``to_dict()``.
    """

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
        """Convert the card to a plain dict suitable for JSON logging."""

        return asdict(self)


def get_experiment_1_baseline() -> ExperimentCard:
    """Return a baseline ExperimentCard for ``data/experiment_1``.

    This describes the paper:
      "Predicting bacteriophage hosts based on sequences of annotated
      receptor-binding proteins" (Scientific Reports, 2021)
    and its main machine-learning host prediction experiment.
    """

    exp_root = _DATA_DIR / "experiment_1"
    pdf_path = exp_root / "paper.pdf"
    repo_root = exp_root / "BacteriophageHostPrediction"

    paper = {
        "title": "Predicting bacteriophage hosts based on sequences of annotated receptor-binding proteins",
        "venue": "Scientific Reports",
        "year": 2021,
        "doi": "10.1038/s41598-021-81063-4",
        "pdf_path": str(pdf_path),
    }

    experiment = {
        "id": "exp1_main_ml_vs_blast",
        "type": "figure",
        "name": "RBP-based host prediction performance vs BLASTp",
        "goal": (
            "Reproduce the precisionrecall performance of the machine-learning host "
            "prediction model based on receptor-binding proteins and compare it to BLASTp."
        ),
        "source_location": {
            "page_hint": 1,
            "section_hint": "Results",
        },
    }

    task = {
        "problem_type": "phage_host_prediction",
        "description": (
            "Predict bacterial hosts from receptor-binding protein sequences using "
            "machine learning and compare performance to BLASTp."
        ),
        "input_modality": ["protein_sequence"],
        "output_modality": ["host_label"],
    }

    phage_system = PhageSystem(
        phage=PhageInfo(
            notes=[
                "RBP database focused on ESKAPE pathogens, Escherichia coli, "
                "Salmonella enterica and Clostridium difficile.",
            ],
        ),
        host=HostInfo(
            species="multiple bacterial species",
        ),
    )

    assay = Assay(
        type="in_silico_host_prediction",
        description=(
            "Train and evaluate a supervised machine learning model on RBP features, "
            "reporting precisionrecall AUC for different sequence similarity regimes."
        ),
        moi=None,
        details={
            "code_root": str(repo_root),
            "notebook": "RBP_host_prediction.ipynb",
            "database_csv": "RBP_database.csv",
        },
    )

    metrics = [
        ExperimentMetric(
            name="PR_AUC",
            type="scalar",
            higher_is_better=True,
            paper_value=None,  # To be filled from paper/figures using readers
            unit="percent",
            tolerance=1.0,
            reported_location=None,
        )
    ]

    artifacts = {
        "target_figures": [],
        "target_tables": [],
        "comparison_points": [
            "overall PR-AUC of ML model",
            "PR-AUC of BLASTp baseline",
        ],
    }

    constraints = {
        "max_runtime_minutes": 240,
        "max_gpus": 0,
        "must_match_implementation_details": True,
    }

    notes = [
        "Baseline configuration for data/experiment_1 BacteriophageHostPrediction repository.",
        "Specific numeric target values (PR-AUC per similarity regime) should be extracted "
        "from the paper and figures using document_reader and vision_reader.",
    ]

    return ExperimentCard(
        paper=paper,
        experiment=experiment,
        task=task,
        phage_system=phage_system,
        assay=assay,
        dataset={},
        model={},
        metrics=metrics,
        artifacts=artifacts,
        constraints=constraints,
        notes=notes,
    )
