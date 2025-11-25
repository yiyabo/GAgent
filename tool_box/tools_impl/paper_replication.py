from __future__ import annotations

from typing import Any, Dict

from app.services.paper_replication import ExperimentCard, get_experiment_1_baseline


async def paper_replication_handler(experiment_id: str = "experiment_1") -> Dict[str, Any]:
    """Load a structured ExperimentCard for a known paper experiment.

    This tool provides a machine-readable specification of a replication
    target. For now it only supports ``experiment_1`` (the
    BacteriophageHostPrediction study in ``data/experiment_1``).
    """

    if experiment_id == "experiment_1":
        card: ExperimentCard = get_experiment_1_baseline()
        return {
            "tool": "paper_replication",
            "success": True,
            "experiment_id": experiment_id,
            "card": card.to_dict(),
        }

    return {
        "tool": "paper_replication",
        "success": False,
        "experiment_id": experiment_id,
        "error": "Unknown experiment_id. Currently only 'experiment_1' is supported.",
        "code": "unknown_experiment",
    }


paper_replication_tool: Dict[str, Any] = {
    "name": "paper_replication",
    "description": (
        "Load structured metadata (ExperimentCard) for a specific paper replication "
        "experiment. Currently supports 'experiment_1' (bacteriophage host prediction "
        "based on RBP sequences). Use this before calling claude_code so that the "
        "code assistant receives a precise, machine-readable experiment spec."
    ),
    "category": "paper_replication",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "experiment_id": {
                "type": "string",
                "description": "Identifier of the replication experiment (e.g. 'experiment_1').",
                "default": "experiment_1",
            }
        },
        "required": [],
    },
    "handler": paper_replication_handler,
    "tags": ["phage", "paper", "replication", "experiment"],
    "examples": [
        "Load the ExperimentCard for experiment_1 and then instruct claude_code to reproduce it.",
    ],
}
