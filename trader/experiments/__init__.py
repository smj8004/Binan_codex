"""
Edge Detection Experiment Framework

Scientific validation framework for trading strategy edge:
- Cost Stress Test: Execution cost sensitivity analysis
- Walk-Forward Validation: Time-based robustness testing
- Regime Gating: Market regime conditional performance
"""

from trader.experiments.core import (
    ExperimentConfig,
    ExperimentResult,
    ExperimentType,
    ScenarioResult,
)
from trader.experiments.cost_stress import CostStressExperiment
from trader.experiments.walk_forward import WalkForwardExperiment
from trader.experiments.regime_gate import RegimeGateExperiment
from trader.experiments.report import ExperimentReporter
from trader.experiments.runner import (
    EdgeRunOutput,
    SystemBatchOutput,
    SystemCandidate,
    default_system_candidates,
    run_edge_validation,
    run_system_batch,
)

__all__ = [
    "ExperimentConfig",
    "ExperimentResult",
    "ExperimentType",
    "ScenarioResult",
    "CostStressExperiment",
    "WalkForwardExperiment",
    "RegimeGateExperiment",
    "ExperimentReporter",
    "EdgeRunOutput",
    "SystemCandidate",
    "SystemBatchOutput",
    "default_system_candidates",
    "run_edge_validation",
    "run_system_batch",
]
