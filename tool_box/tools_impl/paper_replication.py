from __future__ import annotations

from typing import Any, Dict, List

from app.services.paper_replication import (
    ExperimentCard,
    list_experiment_cards,
    load_experiment_card,
)


async def paper_replication_handler(experiment_id: str = "experiment_1") -> Dict[str, Any]:
    """Load a structured ExperimentCard for a known paper experiment."""
    try:
        card: ExperimentCard = load_experiment_card(experiment_id)
        return {
            "tool": "paper_replication",
            "success": True,
            "experiment_id": experiment_id,
            "card": card.to_dict(),
        }
    except FileNotFoundError:
        available: List[Dict[str, Any]] = list_experiment_cards()
        return {
            "tool": "paper_replication",
            "success": False,
            "experiment_id": experiment_id,
            "error": f"Card not found for experiment_id '{experiment_id}'. Use generate_experiment_card or add data/{experiment_id}/card.yaml.",
            "code": "card_missing",
            "available_experiments": available,
        }
    except ValueError as exc:
        return {
            "tool": "paper_replication",
            "success": False,
            "experiment_id": experiment_id,
            "error": f"Card validation failed: {exc}",
            "code": "card_invalid",
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "tool": "paper_replication",
            "success": False,
            "experiment_id": experiment_id,
            "error": f"Unexpected error loading card: {exc}",
            "code": "card_error",
        }


paper_replication_tool: Dict[str, Any] = {
    "name": "paper_replication",
    "description": (
        "Load structured metadata (ExperimentCard) for a specific paper replication "
        "experiment. Cards are stored at data/<experiment_id>/card.yaml. "
        "Use this before calling claude_code so that the code assistant receives a "
        "precise, machine-readable experiment spec. If the card is missing, generate "
        "it with generate_experiment_card."
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
