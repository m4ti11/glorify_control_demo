import random


def backoff(attempt: int, base: float, maximum: float) -> float:
    ceiling = min(maximum, base * 2 ** min(max(attempt - 1, 0), 30))
    return random.uniform(ceiling / 2, ceiling)
