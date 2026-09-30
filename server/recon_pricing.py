"""USD estimates from actual SDK token categories, never a provider invoice."""
from datetime import datetime, timezone
import math

PRICE_DATE = "2026-09-30"
PRICE_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
_OFF_PEAK = {
    "deepseek-flash": {"uncached_input": .15, "cached_input": .003, "output": .6},
    "deepseek-v4-pro": {"uncached_input": .66, "cached_input": .022, "output": 1.98},
}


def rates(provider: str, model: str, at: float, override=None):
    if override is not None:
        if (set(override) != {"uncached_input", "cached_input", "output"}
                or any(type(v) not in (float, int) or not math.isfinite(v) or v < 0
                       for v in override.values())):
            raise ValueError("invalid USD per million token rates")
        return dict(override)
    if provider != "deepseek-official":
        return None
    if model in {"deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}:
        model = "deepseek-flash"
    base = _OFF_PEAK.get(model)
    if base is None:
        return None
    date = datetime.fromtimestamp(at, timezone.utc)
    peak = date.weekday() < 5 and (1 <= date.hour < 4 or 6 <= date.hour < 10)
    return {key: value * (2 if peak else 1) for key, value in base.items()}


def estimate(usage: dict, provider: str, model: str, at: float, override=None):
    price = rates(provider, model, at, override)
    return None if price is None else sum(usage.get(key, 0) * value / 1_000_000
                                         for key, value in price.items())
