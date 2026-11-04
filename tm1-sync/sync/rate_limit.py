import time


class TokenBucket:
    """
    Very simple token bucket for 'N requests per window_ms'.
    We refill continuously so it behaves like a rolling window.
    """

    def __init__(self, capacity: int, window_ms: int):
        self.capacity = max(1, int(capacity))
        self.window_s = max(0.1, float(window_ms) / 1000.0)

        self.tokens = float(self.capacity)
        self.last = time.monotonic()

        # refill rate tokens/sec
        self.rate = float(self.capacity) / self.window_s

    def take(self, n: int = 1) -> None:
        n = max(1, int(n))
        while True:
            now = time.monotonic()
            elapsed = now - self.last
            if elapsed > 0:
                self.tokens = min(float(self.capacity), self.tokens + elapsed * self.rate)
                self.last = now

            if self.tokens >= n:
                self.tokens -= n
                return

            # sleep just enough to accumulate missing tokens
            missing = n - self.tokens
            sleep_s = max(0.01, missing / self.rate)
            time.sleep(sleep_s)