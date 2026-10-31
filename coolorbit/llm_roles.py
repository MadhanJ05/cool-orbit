"""Which Claude model each agent role uses (configs/llm_roles.json), and a one-call helper.

    from coolorbit import llm_roles
    response, cost_usd = llm_roles.create("referee_llm_audit", messages=[{"role": "user", "content": "..."}],
                                          system="You are a referee ...")

``create`` uses the active API key with automatic primary->backup failover (coolorbit/llm_keys.py), the role's
model and effort, and, for Fable 5.1 / Opus 5.5 / Sonnet 5.5, server-side refusal fallbacks
(``fallbacks="default"``). Always check ``response.stop_reason`` (``"refusal"`` means the whole fallback chain
declined) before reading ``response.content``. Log ``cost_usd`` (e.g. to the eval costs.jsonl).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from coolorbit import llm_keys

CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "llm_roles.json"
FALLBACK_BETA = "server-side-fallback-2026-07-01"  # pairs with the scalar form fallbacks="default"


@lru_cache(maxsize=None)
def load(path: Optional[str] = None) -> dict:
    return json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8"))


def role(name: str, path: Optional[str] = None) -> dict:
    roles = load(path)["roles"]
    if name not in roles:
        raise KeyError(f"unknown LLM role {name!r}; known: {sorted(roles)}")
    return roles[name]


def request_kwargs(name: str, path: Optional[str] = None) -> dict:
    """Keyword arguments for ``client.beta.messages.create`` (add ``messages`` / ``system`` / ``tools``)."""
    r = role(name, path)
    kwargs: dict[str, Any] = {"model": r["model"], "max_tokens": r["max_tokens"],
                              "output_config": {"effort": r["effort"]}}
    if r.get("fallbacks"):
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"
    return kwargs


def cost_usd(model: str, usage: Any, path: Optional[str] = None) -> float:
    """Approximate cost of one response from its ``usage`` (input, cache reads, output)."""
    price = load(path)["prices_per_mtok"][model]
    get = (lambda k: getattr(usage, k, 0) or 0) if not isinstance(usage, dict) else (lambda k: usage.get(k, 0) or 0)
    return (get("input_tokens") * price["input"] + get("cache_read_input_tokens") * price["cache_read"]
            + get("output_tokens") * price["output"]) / 1e6


def create(name: str, **request: Any) -> tuple[Any, float]:
    """One Claude call for a role, with key failover. Returns (response, approximate cost in USD)."""
    kwargs = {**request_kwargs(name), **request}
    response = llm_keys.call_with_failover(lambda client: client.beta.messages.create(**kwargs))
    return response, cost_usd(getattr(response, "model", kwargs["model"]) or kwargs["model"], response.usage)
