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


# Optional per-task accounting (set by experiment runners, e.g. scripts/run_arm_c.py). When COSTS_ENV is set,
# every call is appended to that costs.jsonl (schema of coolorbit/eval/arms.md section 3); when BUDGET_ENV is
# also set, a call is refused once the task's recorded spend reaches the budget (fixed budget per experiment).
COSTS_ENV, TASK_ENV, BUDGET_ENV = "COOLORBIT_COSTS_FILE", "COOLORBIT_TASK_ID", "COOLORBIT_BUDGET_USD"


class BudgetExceeded(RuntimeError):
    """This task already spent its LLM budget; the call was not made."""


def spent_usd(costs_path: str | Path, task_id: str) -> float:
    path = Path(costs_path)
    if not path.exists():
        return 0.0
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if str(row.get("task_id")) == str(task_id) and row.get("usd") is not None:
                total += float(row["usd"])
    return total


def _append_cost(path: Path, row: dict) -> None:
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(row) + "\n")
        fcntl.flock(fh, fcntl.LOCK_UN)


def create(name: str, agent: Optional[str] = None, **request: Any) -> tuple[Any, float]:
    """One Claude call for a role, with key failover. Returns (response, approximate cost in USD).

    Honors the per-task accounting environment (see COSTS_ENV / TASK_ENV / BUDGET_ENV above).
    """
    import os

    costs_path, task_id, budget = os.environ.get(COSTS_ENV), os.environ.get(TASK_ENV, ""), os.environ.get(BUDGET_ENV)
    if costs_path and budget is not None and spent_usd(costs_path, task_id) >= float(budget):
        raise BudgetExceeded(f"task {task_id!r} has spent its ${float(budget):.2f} LLM budget")
    kwargs = {**request_kwargs(name), **request}
    response = llm_keys.call_with_failover(lambda client: client.beta.messages.create(**kwargs))
    model = getattr(response, "model", None) or kwargs["model"]
    usd = cost_usd(model, response.usage)
    if costs_path:
        usage = response.usage
        _append_cost(Path(costs_path), {
            "agent": agent or name, "task_id": task_id, "kind": "llm", "model": model,
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "gpu_hours": 0.0, "usd": round(usd, 6), "role": name})
    return response, usd
