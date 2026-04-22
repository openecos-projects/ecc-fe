"""Step metrics analysis — mirrors chipcompiler/analysis/step.py in ecos-studio/ecc."""

from __future__ import annotations

from fecompiler.data.step import StepMetrics, load_metrics, save_metrics
from fecompiler.data.workspace import WorkspaceStep


class StepMetricsBuilder:
    """Load and save step metrics for a given WorkspaceStep.

    Metrics are written by concrete step runners (prepare / slang / verilator).
    This class keeps a stable load/save interface for callers.
    """

    def load(self, step: WorkspaceStep) -> StepMetrics:
        """Load step metrics from step.analysis['metrics']."""
        return load_metrics(step.analysis["metrics"])

    def save(self, step: WorkspaceStep, metrics: StepMetrics) -> None:
        """Persist step metrics to disk."""
        save_metrics(metrics)
