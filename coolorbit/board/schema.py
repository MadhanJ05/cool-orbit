"""Payload schemas for every artifact type on the results board.

Each artifact type has one strict pydantic model. ``Board.post`` validates the
payload before anything is written, so malformed records never reach the board.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Tier = Literal["trust", "discovery"]
CheckType = Literal["method_audit", "premise_check", "second_mlip", "classical_md", "literature_anchor"]
Verdict = Literal["pass", "fail", "agree", "disagree", "reject", "inconclusive"]
NEGATIVE_VERDICTS = frozenset({"fail", "disagree", "reject"})
POSITIVE_VERDICTS = frozenset({"pass", "agree"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Method(BaseModel):
    """How a number was produced. Extra keys (thermostat, box size, ...) are kept."""

    model_config = ConfigDict(extra="allow")

    engine: str = Field(min_length=1, description="ALCHEMI | OpenMM | GROMACS | AMBER | LAMMPS | ...")
    model: str = Field(min_length=1, description="MACE-MPA-0 | AIMNet2 | UMA | GAFF2 | EAM ...")
    protocol: str = Field(min_length=1, description="two-phase coexistence | hysteresis | single-phase heating | ...")
    ensemble: Optional[str] = None
    timestep_fs: Optional[float] = Field(default=None, gt=0)
    n_atoms: Optional[int] = Field(default=None, gt=0)
    sim_length_ps: Optional[float] = Field(default=None, gt=0)
    seeds: Optional[int] = Field(default=None, ge=1)


class Candidate(_Strict):
    """A material proposed by a Scout."""

    name: str = Field(min_length=1)
    family: str = Field(min_length=1)
    tier: Tier
    formula: Optional[str] = None
    structure_source: Optional[str] = Field(default=None, description="e.g. Materials Project id, PubChem CID, CSD refcode")
    rationale: str = ""


class PropertyResult(_Strict):
    """One computed property value (the results-board record).

    There is deliberately no ``reference`` field: the experimental anchor comes
    from an independent ``reference_value`` artifact, never from the producer
    (self-validation is one of the traps we guard against).
    """

    candidate: str = Field(min_length=1)
    family: str = Field(min_length=1)
    tier: Tier
    property: str = Field(min_length=1, description="melting_point | latent_heat | density | conductivity | ...")
    value: float
    units: str = Field(min_length=1)
    uncertainty: Optional[float] = Field(default=None, ge=0)
    method: Method
    flags: list[str] = Field(default_factory=list)
    note: str = ""


class Check(_Strict):
    """A referee's attempt to prove a result wrong. Parent = the result it checks."""

    check_type: CheckType
    verdict: Verdict
    value: Optional[float] = None
    units: Optional[str] = None
    uncertainty: Optional[float] = Field(default=None, ge=0)
    method: Optional[Method] = None
    flags: list[str] = Field(default_factory=list)
    note: str = ""

    @model_validator(mode="after")
    def _numbers_need_context(self) -> "Check":
        if self.value is not None and not self.units:
            raise ValueError("a check that reports a value must state its units")
        if self.value is not None and self.check_type in ("second_mlip", "classical_md") and self.method is None:
            raise ValueError("an independent recomputation must record its method")
        return self


class ReferenceValue(_Strict):
    """A measured value found by the Librarian. Never invent one: no source -> value must be None."""

    candidate: str = Field(min_length=1)
    property: str = Field(min_length=1)
    value: Optional[float]
    units: str = Field(min_length=1)
    uncertainty: Optional[float] = Field(default=None, ge=0)
    source: Optional[str] = None
    note: str = ""

    @model_validator(mode="after")
    def _no_unsourced_values(self) -> "ReferenceValue":
        if self.value is not None and not (self.source and self.source.strip()):
            raise ValueError("a reference value needs a source; record value=None if none was found")
        return self


class RankingEntry(_Strict):
    candidate: str = Field(min_length=1)
    score: float
    result_ids: list[str] = Field(min_length=1)
    note: str = ""


class Ranking(_Strict):
    """The Ranker's shortlist. Entries must cite the results they rely on."""

    criteria: str = Field(min_length=1)
    entries: list[RankingEntry]


class NeedClaim(_Strict):
    """'I am working on need #need_index of target_artifact_id until expires_at.'"""

    target_artifact_id: str = Field(min_length=1)
    need_index: int = Field(ge=0)
    expires_at: str = Field(min_length=1, description="ISO 8601 UTC")


class RunReport(_Strict):
    """A run that did not produce a result: failed (e.g. bracket missed) or refused (e.g. model out of domain).

    Failures are evidence too: they are posted, never dropped. A report may carry
    needs (a retry with a shifted bracket, or a request for a different engine).
    """

    status: Literal["failed", "refused"]
    task: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    run_dir: Optional[str] = None
    attempt: int = Field(default=1, ge=1)
    details: dict = Field(default_factory=dict)


class Request(_Strict):
    """A follow-up ask that carries needs, parented to the artifact it is about (e.g. a candidate)."""

    note: str = ""


class Need(BaseModel):
    """An open request broadcast on an artifact (keys compatible with ScienceClaw's NeedItem)."""

    model_config = ConfigDict(extra="allow")

    artifact_type: str = Field(min_length=1, description="what would satisfy it: check | reference_value | property_result ...")
    query: str = Field(min_length=5)
    rationale: str = Field(min_length=10)
    check_type: Optional[str] = None


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "candidate": Candidate,
    "property_result": PropertyResult,
    "check": Check,
    "reference_value": ReferenceValue,
    "ranking": Ranking,
    "need_claim": NeedClaim,
    "run_report": RunReport,
    "request": Request,
}
