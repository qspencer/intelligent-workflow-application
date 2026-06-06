"""Bedrock model pricing.

Prices are per-1M tokens, separated by input vs. output. Hardcoded for Week 8;
treat as a default that operators override via the `WORKFLOW_PLATFORM_PRICING`
env var (JSON, same shape as `MODEL_PRICING`) when prices change.

These are AWS Bedrock list prices for the Anthropic Claude family. They drift
over time; check https://aws.amazon.com/bedrock/pricing/ for current numbers.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPrice:
    """Per-1M-token prices in USD."""

    input_per_million: float
    output_per_million: float


# Regional inference-profile prefixes Bedrock applies in front of a model id
# (us.foo, eu.foo, apac.foo, global.foo). `cost_for_usage` strips these before
# lookup so the table only needs the bare model id.
_REGION_PREFIXES: tuple[str, ...] = ("us.", "eu.", "apac.", "global.")

# Defaults (USD per 1M tokens). Tracker can override via env at startup.
_DEFAULT_PRICING: dict[str, ModelPrice] = {
    # Claude 3 family
    "anthropic.claude-3-haiku-20240307-v1:0": ModelPrice(0.25, 1.25),
    "anthropic.claude-3-sonnet-20240229-v1:0": ModelPrice(3.00, 15.00),
    "anthropic.claude-3-opus-20240229-v1:0": ModelPrice(15.00, 75.00),
    # Claude 3.5 family
    "anthropic.claude-3-5-sonnet-20240620-v1:0": ModelPrice(3.00, 15.00),
    "anthropic.claude-3-5-sonnet-20241022-v2:0": ModelPrice(3.00, 15.00),
    "anthropic.claude-3-5-haiku-20241022-v1:0": ModelPrice(0.80, 4.00),
    # Claude 4 family — Bedrock requires invocation via a regional inference
    # profile, so callers pass e.g. "us.anthropic.claude-haiku-4-5-...". The
    # lookup in cost_for_usage strips the region prefix before matching here.
    "anthropic.claude-haiku-4-5-20251001-v1:0": ModelPrice(1.00, 5.00),
    "anthropic.claude-sonnet-4-6": ModelPrice(3.00, 15.00),
    "anthropic.claude-opus-4-6": ModelPrice(5.00, 25.00),
    "anthropic.claude-opus-4-7": ModelPrice(5.00, 25.00),
}


def _load_pricing() -> dict[str, ModelPrice]:
    raw = os.environ.get("WORKFLOW_PLATFORM_PRICING")
    if not raw:
        return dict(_DEFAULT_PRICING)
    try:
        overrides = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("WORKFLOW_PLATFORM_PRICING is not valid JSON; using defaults")
        return dict(_DEFAULT_PRICING)
    merged = dict(_DEFAULT_PRICING)
    for key, val in overrides.items():
        if isinstance(val, dict) and "input_per_million" in val and "output_per_million" in val:
            merged[str(key)] = ModelPrice(
                float(val["input_per_million"]), float(val["output_per_million"])
            )
    return merged


MODEL_PRICING: dict[str, ModelPrice] = _load_pricing()


def _strip_region_prefix(model_id: str) -> str:
    for prefix in _REGION_PREFIXES:
        if model_id.startswith(prefix):
            return model_id[len(prefix) :]
    return model_id


def price_for_model(model_id: str) -> ModelPrice | None:
    """Per-1M-token rates for a model, or None if unpriced. Matches the literal
    id first, then the bare id after stripping a regional inference-profile
    prefix — same lookup as `cost_for_usage`."""
    return MODEL_PRICING.get(model_id) or MODEL_PRICING.get(_strip_region_prefix(model_id))


def cost_for_usage(usage: dict[str, int] | None, model_id: str) -> float:
    """Compute USD cost from an `AgentUsage`-shaped dict and a Bedrock model id.

    Tries the literal id first, then falls back to the bare id after stripping
    a leading regional inference-profile prefix. Returns 0.0 if usage is empty
    or the model is unpriced (with a debug log so the gap is visible to
    operators)."""
    if not usage:
        return 0.0
    price = MODEL_PRICING.get(model_id) or MODEL_PRICING.get(_strip_region_prefix(model_id))
    if price is None:
        logger.debug("No pricing for model %r — recording cost=0.0", model_id)
        return 0.0
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    return (
        input_tokens * price.input_per_million / 1_000_000
        + output_tokens * price.output_per_million / 1_000_000
    )
