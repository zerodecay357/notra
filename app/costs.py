"""Rough cost + environmental-impact estimates for a Claude API call.

Two very different kinds of number live in this file:

1. **Cost** — this is a real calculation. The $/token figures below should be
   checked against https://www.anthropic.com/pricing before you rely on the
   numbers (prices change, and per-tier figures here are set from the
   published pattern at time of writing — update PRICING if they drift).
   Cache-write/cache-read multipliers (1.25x / 0.1x of base input price) are
   the actual Anthropic prompt-caching ratios.

2. **Energy / CO2** — no AI provider publishes per-token energy or carbon
   numbers, so this is *not* a measurement. It's an order-of-magnitude
   estimate built from public research on transformer-inference energy use
   (figures in the same ballpark as Luccioni et al. "Power Hungry Processing"
   and de Vries "The growing energy footprint of AI", which put a single
   large-model text response around 1-3 Wh) combined with the global average
   grid carbon intensity (~480 gCO2/kWh, IEA/Ember). Treat every number this
   half of the file produces as illustrative, not authoritative — the true
   figure depends on the provider's actual hardware, data-centre location,
   and energy mix, none of which are public.
"""

from __future__ import annotations

# ----------------------------------------------------------------- pricing --
# USD per million tokens: (input, output). Verify against each provider's
# pricing page — Anthropic tiers follow the historical Claude pattern,
# Gemini figures are the standard paid-tier rates (the free tier is $0;
# the estimate still shows what the call *would* cost).
_PRICE_TIERS: dict[str, tuple[float, float]] = {
    "claude-opus":   (15.00, 75.00),
    "claude-sonnet": (3.00, 15.00),
    "claude-haiku":  (0.80, 4.00),
    "claude-fable":  (1.00, 5.00),
    "gemini-2.5-pro":   (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini": (0.30, 2.50),  # fallback for other Gemini models
}
_DEFAULT_TIER = "claude-sonnet"

_CACHE_WRITE_MULT = 1.25   # writing to the cache costs a bit more than a plain input token
_CACHE_READ_MULT = 0.10    # Anthropic: cached read ≈ a tenth of input price
_CACHE_READ_MULT_GEMINI = 0.25  # Gemini implicit caching: 75% discount

# ------------------------------------------------------------------ energy --
# Wh per token, split input (prefill, parallelisable → cheaper) vs output
# (decode, sequential → pricier). Tiered by rough model size/depth.
_ENERGY_TIERS: dict[str, tuple[float, float]] = {
    # (Wh/input_token, Wh/output_token)
    "claude-opus":   (0.0015, 0.0060),
    "claude-sonnet": (0.0006, 0.0025),
    "claude-haiku":  (0.00025, 0.0010),
    "claude-fable":  (0.0004, 0.0016),
    "gemini-2.5-pro":   (0.0006, 0.0025),
    "gemini-2.5-flash": (0.00025, 0.0010),
    "gemini": (0.00025, 0.0010),
}

GRID_G_CO2_PER_KWH = 480.0   # global average grid carbon intensity (IEA/Ember, rough)

# Relatable equivalents (rough public figures, for context only).
_CAR_G_PER_KM = 251.0        # EPA-style average passenger car
_PHONE_G_PER_CHARGE = 8.0    # full smartphone charge on an average grid
_TREE_G_PER_DAY = 21000.0 / 365.0  # a mature tree absorbs ~21kg CO2/year


def _tier(model: str, table: dict) -> tuple[float, float]:
    for prefix, values in table.items():
        if model.startswith(prefix):
            return values
    return table.get(_DEFAULT_TIER) or next(iter(table.values()))


def merge_usage(*usages: dict) -> dict:
    """Sum several usage dicts (e.g. a generate() call plus a repair() call)."""
    out = {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0}
    for u in usages:
        for k in out:
            out[k] += (u or {}).get(k, 0)
    return out


def estimate(model: str, usage: dict) -> dict:
    """Return cost_usd, energy_wh, co2_g, and a human equivalents dict."""
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_creation = usage.get("cache_creation_tokens", 0)
    cache_read = usage.get("cache_read_tokens", 0)

    read_mult = _CACHE_READ_MULT_GEMINI if model.startswith("gemini") else _CACHE_READ_MULT
    price_in, price_out = _tier(model, _PRICE_TIERS)
    cost_usd = (
        input_tokens * price_in
        + cache_creation * price_in * _CACHE_WRITE_MULT
        + cache_read * price_in * read_mult
        + output_tokens * price_out
    ) / 1_000_000

    energy_in, energy_out = _tier(model, _ENERGY_TIERS)
    # Cached tokens are read, not recomputed, so they're cheap on energy too —
    # charge them at the same discount ratio used for cost.
    energy_wh = (
        input_tokens * energy_in
        + cache_creation * energy_in * _CACHE_WRITE_MULT
        + cache_read * energy_in * read_mult
        + output_tokens * energy_out
    )

    co2_g = energy_wh / 1000.0 * GRID_G_CO2_PER_KWH

    return {
        "cost_usd": round(cost_usd, 6),
        "energy_wh": round(energy_wh, 4),
        "co2_g": round(co2_g, 4),
        "equivalents": {
            "car_km": round(co2_g / _CAR_G_PER_KM, 4),
            "phone_charges": round(co2_g / _PHONE_G_PER_CHARGE, 3),
            "tree_hours": round(co2_g / _TREE_G_PER_DAY * 24, 2),
        },
    }
