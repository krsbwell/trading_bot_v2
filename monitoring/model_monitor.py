"""
Health-check view over learning.model_registry — which version is in
production, when it was promoted, and whether anything is stuck in
candidate/testing for a long time. Read-only reporting only.
"""
from learning.model_registry import ModelRegistry


def status(registry: "ModelRegistry | None" = None) -> dict:
    registry = registry or ModelRegistry()
    production = registry.production_version()
    return {
        "production_version": production,
        "production_info": registry.get(production) if production else None,
        "candidates": registry.list_by_stage("candidate"),
        "testing": registry.list_by_stage("testing"),
        "validated": registry.list_by_stage("validated"),
        "rejected": registry.list_by_stage("rejected"),
    }
