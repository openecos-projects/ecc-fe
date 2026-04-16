"""Step metrics analysis — mirrors chipcompiler/analysis/step.py in ecos-studio/ecc."""

from __future__ import annotations

from fecompiler.data.step import StepMetrics, load_metrics, save_metrics
from fecompiler.data.workspace import WorkspaceStep


class StepMetricsBuilder:
    """Load and save step metrics for a given WorkspaceStep.

    In ecc-fe all steps are stubs, so metrics files contain placeholder data
    written by EngineFlow._run_stub_step().  This class provides the same
    interface as ecc so callers can be ported without changes.
    """

    def load(self, step: WorkspaceStep) -> StepMetrics:
        """Load step metrics from step.analysis['metrics']."""
        return load_metrics(step.analysis["metrics"])

    def save(self, step: WorkspaceStep, metrics: StepMetrics) -> None:
        """Persist step metrics to disk."""
        save_metrics(metrics)
