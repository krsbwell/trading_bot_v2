"""
Evaluation facade — re-exports this project's existing backtest/walk-forward
machinery (backtest.runner, engine.wfo_optimizer) under the `agents.*`
surface. Deliberately thin: does not reimplement or wrap their signatures,
since doing so risks drifting out of sync with the real backtester. Import
from here when writing new adaptive-strategy tooling; import from
backtest.runner / engine.wfo_optimizer directly everywhere else, as this
codebase already does.
"""
from backtest.runner import run_backtest, run_walk_forward
from engine.wfo_optimizer import WFOOptimizer, wfo_optimizer

__all__ = ["run_backtest", "run_walk_forward", "WFOOptimizer", "wfo_optimizer"]
