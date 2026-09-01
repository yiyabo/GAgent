"""Artifact event stream: one fact source for every produced artifact.

Producers (chat tool execution, plan task completion, audit repair) emit
``artifact.produced`` events; the RegistryProjector consumes them and owns all
derived views (registry.json, deliverables/latest/, manifest_latest.json).
"""

from .events import ArtifactEvent, append_events, iter_events
from .registry import RegistryStore

__all__ = ["ArtifactEvent", "RegistryStore", "append_events", "iter_events"]
