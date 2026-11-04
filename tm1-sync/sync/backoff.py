import random
from datetime import datetime, timedelta, timezone


def compute_next_retry_at(attempts: int) -> datetime:
    """
    Exponential backoff with jitter, capped at 30 minutes.
    attempts is the NEXT attempts value (i.e., after increment).
    """
    # Base schedule (seconds)
    schedule = [10, 30, 120, 300, 600, 1200, 1800]  # cap 30m
    idx = max(0, min(len(schedule) - 1, attempts - 1))
    base = schedule[idx]
    jitter = random.randint(0, max(1, base // 5))  # up to 20% jitter
    return datetime.now(timezone.utc) + timedelta(seconds=base + jitter)