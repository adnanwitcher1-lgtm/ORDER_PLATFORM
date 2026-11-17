from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OrderProgress:
    target: int
    observed_increase: Optional[int]   # can be negative if the count dropped below the baseline
    remaining_gap: Optional[int]
    progress_percent: int              # always between 0 and 100
    target_reached: bool
    below_baseline: bool
    has_observation: bool


def calculate_target(baseline: int, requested_increase: int) -> int:
    """Target = Baseline + Requested increase."""
    if baseline < 0:
        raise ValueError("Baseline cannot be negative.")
    if requested_increase <= 0:
        raise ValueError("Requested increase cannot be zero or negative.")
    return baseline + requested_increase


def calculate_progress(baseline: int, requested_increase: int, latest_observed: Optional[int]) -> OrderProgress:
    target = calculate_target(baseline, requested_increase)

    # No observation or the metric is unavailable: never guess a value.
    if latest_observed is None:
        return OrderProgress(
            target=target,
            observed_increase=None,
            remaining_gap=None,
            progress_percent=0,
            target_reached=False,
            below_baseline=False,
            has_observation=False,
        )

    observed_increase = latest_observed - baseline
    remaining_gap = max(target - latest_observed, 0)

    # Integer math (floor): 100% is shown only when the target is really reached.
    percent = (observed_increase * 100) // requested_increase
    percent = max(0, min(100, percent))

    return OrderProgress(
        target=target,
        observed_increase=observed_increase,
        remaining_gap=remaining_gap,
        progress_percent=percent,
        target_reached=latest_observed >= target,
        below_baseline=observed_increase < 0,
        has_observation=True,
    )