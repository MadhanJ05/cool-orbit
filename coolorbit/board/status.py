"""Derive a result's status from the board. No agent can set a status directly.

Trust rule (CLAUDE.md section 10), evaluated against pre-registered tolerances in
``configs/trust_rules.json``. A result is **trusted** only if:

  (a) another agent's method audit passed, AND
  (b) an independent method (different engine/model, different agent) reproduced
      the value within the "agree" tolerance, AND
  (c) for the trust tier, it matches a *sourced* measured value within the
      "tier1" tolerance.

Any negative check, numeric disagreement or unit mismatch makes it **flagged**
(rerun needed). It is **rejected** only when enough *distinct* referees reject it,
so no single referee is a gatekeeper. Agreement is judged from the numbers, not
from the referee's verdict text.

Disputes between method audits: when an adjudicator (``rules["adjudicators"]``, default
``["adjudicator"]``) posts a decisive ``method_audit`` ruling (pass or fail) on a result, the
method audits by other referees posted *before* that ruling are settled by it: they stop
counting and the ruling counts instead. An inconclusive ruling settles nothing. Audits posted
after the ruling, premise checks and independent-method checks always count.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from coolorbit.board._scienceclaw import REPO_ROOT
from coolorbit.board.schema import NEGATIVE_VERDICTS, POSITIVE_VERDICTS

if TYPE_CHECKING:
    from coolorbit.board._scienceclaw import Artifact
    from coolorbit.board.store import BoardSnapshot

DEFAULT_RULES_PATH = REPO_ROOT / "configs" / "trust_rules.json"


@dataclass
class StatusReport:
    status: str  # simulated | under_review | trusted | flagged | rejected
    reasons: list[str] = field(default_factory=list)
    superseded_by: list[str] = field(default_factory=list)


def load_rules(path: Optional[str | Path] = None) -> dict:
    return json.loads(Path(path or DEFAULT_RULES_PATH).read_text(encoding="utf-8"))


def _norm(text: object) -> str:
    return " ".join(str(text or "").lower().split())


def _same_method(a: Optional[dict], b: Optional[dict]) -> bool:
    if not a or not b:
        return False
    return _norm(a.get("engine")) == _norm(b.get("engine")) and _norm(a.get("model")) == _norm(b.get("model"))


def _within(value: float, ref: float, tol: dict) -> bool:
    return abs(value - ref) <= max(tol.get("abs") or 0.0, (tol.get("rel") or 0.0) * abs(ref))


def settled_audits(checks: list["Artifact"], rules: dict, producer: str) -> dict[str, str]:
    """{method_audit id: id of the adjudicator ruling that settles it}. ``checks`` are in board order."""
    adjudicators = set(rules.get("adjudicators", ["adjudicator"]))
    rulings = [c for c in checks if c.producer_agent in adjudicators and c.producer_agent != producer
               and c.payload["check_type"] == "method_audit"
               and c.payload["verdict"] in (NEGATIVE_VERDICTS | POSITIVE_VERDICTS)]
    if not rulings:
        return {}
    ruling = rulings[-1]  # the latest decisive ruling settles everything before it
    key = (ruling.timestamp, ruling.artifact_id)
    return {c.artifact_id: ruling.artifact_id for c in checks
            if c.payload["check_type"] == "method_audit" and c.producer_agent not in adjudicators
            and (c.timestamp, c.artifact_id) < key}


def derive_status(snap: "BoardSnapshot", result: "Artifact | str", rules: dict) -> StatusReport:
    if isinstance(result, str):
        result = snap.artifacts[result]
    if result.artifact_type != "property_result":
        raise ValueError(f"{result.artifact_id} is a {result.artifact_type}, not a property_result")

    p = result.payload
    units = p["units"]
    prop_rules = rules.get("properties", {}).get(p["property"])
    independent_types = set(rules.get("independent_check_types", ["second_mlip", "classical_md"]))
    kids = snap.children.get(result.artifact_id, [])
    checks = [k for k in kids if k.artifact_type == "check"]
    report = StatusReport(
        status="simulated",
        # Only a rerun of the same property replaces a result; a follow-up for another property (e.g. latent heat
        # requested from a melting result) does not.
        superseded_by=[k.artifact_id for k in kids
                       if k.artifact_type == "property_result" and k.payload.get("property") == p["property"]],
    )
    reasons = report.reasons
    failing = False

    if prop_rules is None:
        reasons.append(f"no pre-registered tolerance for '{p['property']}', so it cannot become trusted")
    elif units not in prop_rules.get("units", []):
        failing = True
        reasons.append(f"units '{units}' not allowed for {p['property']} (expected {prop_rules.get('units')})")

    rejecters = sorted({c.producer_agent for c in checks
                        if c.payload["verdict"] == "reject" and c.producer_agent != result.producer_agent})
    if len(rejecters) >= rules.get("rejection_min_referees", 2):
        report.status = "rejected"
        reasons.insert(0, f"rejected by {len(rejecters)} independent referees: {', '.join(rejecters)}")
        return report

    settled = settled_audits(checks, rules, result.producer_agent)

    audit_pass = independent_agree = False
    for c in checks:
        cp, who = c.payload, c.producer_agent
        tag = f"{cp['check_type']} by {who}"
        detail = "".join([f" [{', '.join(cp['flags'])}]" if cp.get("flags") else "",
                          f" ({cp['note']})" if cp.get("note") else ""])
        if who == result.producer_agent:
            reasons.append(f"{tag}: ignored (agents cannot check their own results)")
            continue
        if c.artifact_id in settled:
            reasons.append(f"{tag}: {cp['verdict']}, settled by adjudicator ruling {settled[c.artifact_id][:8]}")
            continue
        if cp["verdict"] in NEGATIVE_VERDICTS:
            failing = True
            reasons.append(f"{tag}: {cp['verdict']}{detail}")
        if cp["check_type"] == "method_audit" and cp["verdict"] == "pass":
            audit_pass = True
            reasons.append(f"{tag}: pass{detail}")
        if cp["check_type"] not in independent_types:
            continue
        if _same_method(cp.get("method"), p["method"]):
            reasons.append(f"{tag}: same engine/model as the result, so not independent")
            continue
        if cp.get("value") is None:
            if cp["verdict"] in POSITIVE_VERDICTS:
                reasons.append(f"{tag}: says '{cp['verdict']}' but reported no value, so not counted")
            continue
        if cp.get("units") != units:
            failing = True
            reasons.append(f"{tag}: unit mismatch ({cp.get('units')} vs {units})")
            continue
        if prop_rules is None:
            continue
        delta = abs(cp["value"] - p["value"])
        if _within(cp["value"], p["value"], prop_rules["agree"]):
            if cp["verdict"] not in NEGATIVE_VERDICTS:
                independent_agree = True
                reasons.append(f"{tag}: agrees, |delta| = {delta:.3g} {units} ({cp['method']['engine']}/{cp['method']['model']})")
        else:
            failing = True
            said = " although the referee said agree" if cp["verdict"] in POSITIVE_VERDICTS else ""
            reasons.append(f"{tag}: |delta| = {delta:.3g} {units} exceeds the agree tolerance{said}")

    tier1_ok = p["tier"] != "trust"
    if p["tier"] == "trust":
        refs = [r for r in snap.of_type("reference_value")
                if _norm(r.payload["candidate"]) == _norm(p["candidate"])
                and r.payload["property"] == p["property"] and r.payload["value"] is not None]
        if not refs:
            reasons.append("trust tier: no sourced measured value on the board yet")
        for r in refs:
            rp = r.payload
            if rp["units"] != units:
                failing = True
                reasons.append(f"reference {r.artifact_id[:8]}: unit mismatch ({rp['units']} vs {units})")
            elif prop_rules is not None:
                delta = abs(p["value"] - rp["value"])
                if _within(p["value"], rp["value"], prop_rules["tier1"]):
                    tier1_ok = True
                    reasons.append(f"matches measured {rp['value']:g} {units} (|delta| = {delta:.3g}) [{rp['source']}]")
                else:
                    failing = True
                    reasons.append(f"off from measured {rp['value']:g} {units} by {delta:.3g} [{rp['source']}]")

    if report.superseded_by:
        reasons.append(f"superseded by rerun(s): {', '.join(i[:8] for i in report.superseded_by)}")

    if failing:
        report.status = "flagged"
    elif prop_rules is not None and audit_pass and independent_agree and tier1_ok:
        report.status = "trusted"
    elif checks:
        report.status = "under_review"
        if not audit_pass:
            reasons.append("still needs a passing method audit from another agent")
        if not independent_agree:
            reasons.append("still needs an agreeing independent method (second MLIP or classical MD)")
    return report


def summarize(snap: "BoardSnapshot", rules: dict) -> list[tuple["Artifact", StatusReport]]:
    """Status of every result on the board, oldest first."""
    return [(r, derive_status(snap, r, rules)) for r in snap.of_type("property_result")]
