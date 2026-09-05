"""Model pricing table.

Loads ``pricing.yaml`` and computes request cost from token counts. Prices are
per 1M tokens. If a model has no pricing entry, cost is 0 (e.g. local Ollama).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ModelPrice:
    input_price_per_1m: float
    output_price_per_1m: float


class PricingTable:
    def __init__(self, prices: dict[str, ModelPrice]) -> None:
        self._prices = prices

    @classmethod
    def load(cls, path: str | Path) -> "PricingTable":
        path = Path(path)
        if not path.exists():
            return cls({})
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        prices: dict[str, ModelPrice] = {}
        for model, entry in (data.get("models") or {}).items():
            prices[model] = ModelPrice(
                input_price_per_1m=float(entry.get("input_price_per_1m", 0.0)),
                output_price_per_1m=float(entry.get("output_price_per_1m", 0.0)),
            )
        return cls(prices)

    def price_for(self, model: str) -> ModelPrice:
        return self._prices.get(model, ModelPrice(0.0, 0.0))

    def cost_usd(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        price = self.price_for(model)
        return (
            prompt_tokens / 1_000_000 * price.input_price_per_1m
            + completion_tokens / 1_000_000 * price.output_price_per_1m
        )
