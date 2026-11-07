"""Real execution budgets. Randomize deadlines, never reported measurements."""
import math
import random


MAX_TASK_SECONDS = 200.0
MAX_ANALYSIS_SECONDS = 195.0
CANCEL_GRACE_SECONDS = 2.0
CLEANUP_SECONDS = 2.0


def analysis_budget(configured_seconds):
    if not math.isfinite(configured_seconds) or configured_seconds <= 0:
        raise ValueError('Analysis budget must be finite and positive.')
    upper = min(configured_seconds, MAX_ANALYSIS_SECONDS)
    # Preserve deliberately short limits used for cancellation/testing. Normal
    # tasks receive one budget, sampled once; fast tasks are never delayed.
    if upper <= 15:
        return upper
    lower = max(upper / 2, upper - 15)
    return round(random.uniform(lower, upper), 6)
