"""Usage tracking and cost limiting for API calls."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import structlog

logger = structlog.get_logger(__name__)

# Cost per 1M tokens (as of 2024)
COST_PER_MILLION = {
    # Embeddings
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
    "text-embedding-ada-002": 0.10,
    # Chat models (input)
    "gpt-4o-mini-input": 0.15,
    "gpt-4o-input": 2.50,
    "gpt-4-turbo-input": 10.00,
    # Chat models (output)
    "gpt-4o-mini-output": 0.60,
    "gpt-4o-output": 10.00,
    "gpt-4-turbo-output": 30.00,
    # Anthropic
    "claude-haiku-4-5-20251001-input": 0.25,
    "claude-haiku-4-5-20251001-output": 1.25,
}

USAGE_FILE = Path(__file__).resolve().parent.parent / "artifacts" / "usage.json"

_lock = Lock()


class UsageLimitExceeded(Exception):
    """Raised when usage limit is exceeded."""
    pass


def _load_usage() -> dict:
    """Load usage data from file."""
    # Import here to avoid circular dependency
    from app.core.config import settings
    
    if USAGE_FILE.exists():
        try:
            with open(USAGE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "total_cost": 0.0,
        "limit": settings.usage_limit,
        "history": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _save_usage(data: dict) -> None:
    """Save usage data to file."""
    USAGE_FILE.parent.mkdir(exist_ok=True)
    with open(USAGE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_usage() -> dict:
    """Get current usage stats."""
    with _lock:
        return _load_usage()


def get_remaining_budget() -> float:
    """Get remaining budget in dollars."""
    usage = get_usage()
    return max(0, usage["limit"] - usage["total_cost"])


def set_limit(limit: float) -> None:
    """Set the usage limit in dollars."""
    with _lock:
        data = _load_usage()
        data["limit"] = limit
        _save_usage(data)
    logger.info("usage limit updated", limit=limit)


def reset_usage() -> None:
    """Reset usage tracking (keeps limit)."""
    from app.core.config import settings
    
    with _lock:
        data = _load_usage()
        limit = data.get("limit", settings.usage_limit)
        _save_usage({
            "total_cost": 0.0,
            "limit": limit,
            "history": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    logger.info("usage reset")


def check_limit() -> bool:
    """Check if we're under the limit. Returns True if OK."""
    usage = get_usage()
    return usage["total_cost"] < usage["limit"]


def check_limit_or_raise() -> None:
    """Check limit and raise UsageLimitExceeded if exceeded."""
    usage = get_usage()
    if usage["total_cost"] >= usage["limit"]:
        raise UsageLimitExceeded(
            f"Usage limit exceeded: ${usage['total_cost']:.4f} / ${usage['limit']:.2f}. "
            "Reset usage or increase limit to continue."
        )


def record_usage(
    model: str,
    tokens: int,
    usage_type: str = "embedding",  # embedding, input, output
) -> float:
    """Record API usage and return cost.
    
    Args:
        model: Model name (e.g., 'text-embedding-3-small', 'gpt-4o-mini')
        tokens: Number of tokens used
        usage_type: 'embedding', 'input', or 'output'
    
    Returns:
        Cost in dollars
    """
    # Determine cost key
    if usage_type == "embedding":
        cost_key = model
    else:
        cost_key = f"{model}-{usage_type}"
    
    # Get cost per million tokens
    cost_per_million = COST_PER_MILLION.get(cost_key, 0.02)  # Default to embedding cost
    cost = (tokens / 1_000_000) * cost_per_million
    
    with _lock:
        data = _load_usage()
        data["total_cost"] += cost
        data["history"].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "type": usage_type,
            "tokens": tokens,
            "cost": cost,
        })
        
        # Keep only last 1000 entries
        if len(data["history"]) > 1000:
            data["history"] = data["history"][-1000:]
        
        _save_usage(data)
    
    logger.debug("usage recorded", model=model, tokens=tokens, cost=cost)
    return cost


def estimate_cost(model: str, tokens: int, usage_type: str = "embedding") -> float:
    """Estimate cost without recording."""
    if usage_type == "embedding":
        cost_key = model
    else:
        cost_key = f"{model}-{usage_type}"
    
    cost_per_million = COST_PER_MILLION.get(cost_key, 0.02)
    return (tokens / 1_000_000) * cost_per_million
