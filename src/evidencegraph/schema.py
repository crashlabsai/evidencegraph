"""Vocabulary required by the frozen DQ/LQ questions; all claims retain their witnesses."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.1"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Outcome(StrEnum):
    SUPPORTED = "supported"
    AMBIGUOUS = "ambiguous"
    CONTRADICTED = "contradicted"
    UNMATCHED = "unmatched"
    NOT_ASSESSABLE = "not_assessable"


class TrustDomainRelation(StrEnum):
    SAME = "same"
    INDEPENDENT = "independent"
    UNKNOWN = "unknown"


class CoverageKind(StrEnum):
    MESSAGE_WEIGHTED = "message_weighted"
    UNIQUE_AUTHOR = "unique_author"
    TRANSCRIPT = "transcript"


class TrustDomain(Frozen):
    id: str
    label: str
    sources: tuple[str, ...] = ()
    related_to: dict[str, TrustDomainRelation] = Field(default_factory=dict)


class ClockBound(Frozen):
    clock_a: str
    clock_b: str
    bound_seconds: float = Field(ge=0)
    source: str = "user declaration"
    note: str = ""


class Population(Frozen):
    id: str
    namespace: str
    time_range: tuple[datetime | None, datetime | None] = (None, None)
    description: str = ""

    @model_validator(mode="after")
    def valid_time_range(self) -> Population:
        lower, upper = self.time_range
        for value in self.time_range:
            if value is not None and value.utcoffset() is None:
                raise ValueError("population endpoints require timezones")
        if lower and upper and lower > upper:
            raise ValueError("reversed population interval")
        return self


class CaseConfig(Frozen):
    title: str
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    trust_domains: tuple[TrustDomain, ...] = ()
    clock_bounds: tuple[ClockBound, ...] = ()
    populations: tuple[Population, ...] = ()
    handle_patterns: tuple[str, ...] = (r"(?m)^Handle:\s*(\S+)",)
    family_confidence: float = Field(default=0.8, ge=0, le=1)

    @model_validator(mode="after")
    def unique_domains(self) -> CaseConfig:
        for pattern in self.handle_patterns:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid handle pattern: {exc}") from exc
            if compiled.groups != 1:
                raise ValueError("handle patterns require exactly one capture group")
        domains = {d.id: d for d in self.trust_domains}
        if len(domains) != len(self.trust_domains):
            raise ValueError("duplicate trust domain")
        for domain in self.trust_domains:
            for other, relation in domain.related_to.items():
                if other not in domains or other == domain.id:
                    raise ValueError("trust relation needs two declared domains")
                reverse = domains[other].related_to.get(domain.id)
                if reverse is not None and reverse != relation:
                    raise ValueError("conflicting trust declarations")
        return self


class Witness(Frozen):
    witness_id: str
    kind: str
    trust_domain: str
    adapter: str
    adapter_version: str = "0.1"
    origin: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    acquired_at: str
    coverage_claim: str = "No completeness claim"
    clock_ids: tuple[str, ...] = ()
    row_count: int | None = None
    snapshot_path: str
    filename: str


class Clock(Frozen):
    clock_id: str
    witness_id: str
    label: str
    note: str = "Source-reported clock; accuracy is not independently established"


class Citation(Frozen):
    citation_id: str
    witness_id: str
    locator_kind: Literal["event", "message", "jsonl_line", "json_path", "csv_row", "file"]
    locator: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview: str = ""
    transcript_id: str | None = None
    event_id: str | None = None
    message_id: str | None = None


EntityKind = Literal[
    "identity", "session", "action", "artifact_version", "message", "substrate", "task", "job"
]
RelationKind = Literal[
    "authored",
    "executed",
    "launched",
    "produced",
    "read",
    "sent",
    "received",
    "built_on",
    "reproduced",
    "same_actor_as",
    "located_on",
    "corroborated_by",
    "member_of",
]


class Entity(Frozen):
    entity_id: str
    kind: EntityKind
    subkind: str
    natural_key: str
    witness_id: str
    citation_id: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    time_lower: str | None = None
    time_upper: str | None = None
    time_grade: str | None = None
    time_uncertainty_s: float | None = Field(default=None, ge=0)
    winning_clock_id: str | None = None


class TimeClaim(Frozen):
    entity_id: str
    witness_id: str
    clock_id: str
    lower: str
    upper: str
    grade: str
    uncertainty_s: float = Field(ge=0)
    winning: bool
    citation_id: str
    note: str = ""

    @model_validator(mode="after")
    def ordered(self) -> TimeClaim:
        a, b = datetime.fromisoformat(self.lower), datetime.fromisoformat(self.upper)
        if a.utcoffset() is None or b.utcoffset() is None or a > b:
            raise ValueError("time claim must be an ordered timezone-aware interval")
        return self


class Relation(Frozen):
    relation_id: str
    kind: RelationKind
    subject_id: str
    object_id: str
    outcome: Outcome
    method: str
    rationale: str
    witness_ids: tuple[str, ...]
    citation_ids: tuple[str, ...]
    candidates: tuple[str, ...] = ()
    trust_domain_relation: TrustDomainRelation = TrustDomainRelation.UNKNOWN
    authenticated: bool = False
    run_id: str = "ingest"

    @model_validator(mode="after")
    def evidence_invariants(self) -> Relation:
        if not self.witness_ids or not self.citation_ids:
            raise ValueError("every relation must cite its witnesses")
        if self.outcome == Outcome.SUPPORTED:
            if self.kind in {"produced", "executed"} and len(self.candidates) != 1:
                raise ValueError("supported production/execution needs one chosen candidate")
            if self.kind in {"produced", "executed"} and self.candidates[0] != self.object_id:
                raise ValueError("chosen candidate must be the supported relation's object")
            if self.kind == "same_actor_as" and not self.authenticated:
                raise ValueError("same actor requires authenticated identities on both sides")
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("duplicate candidates")
        return self


class CoverageEstimate(Frozen):
    id: str
    kind: CoverageKind
    reference_source: str
    population: Population
    sampling_unit: str
    design: str
    estimate: float | None = None
    interval: tuple[float, float] | None = None
    interval_level: float = Field(default=0.95, gt=0, lt=1)
    n_population: int
    n_sampled: int
    n_supported: int
    ambiguous_handling: str = "kept in denominator, excluded from numerator"
    not_assessable_handling: str = "excluded from denominator"
    exclusions: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    run_id: str = "coverage"


class CaptureRecapture(Frozen):
    id: str
    witness_a: str
    witness_b: str
    unit_kind: str
    n_a: int = Field(ge=0)
    n_b: int = Field(ge=0)
    m_both: int = Field(ge=0)
    n_hat_chapman: float | None = None
    interval: tuple[float, float] | None = None
    level: float = Field(default=0.95, gt=0, lt=1)
    assumptions: tuple[str, ...]
    assumption_violations: tuple[str, ...] = ()
    interpretation: str = "Model-dependent estimate; not an identified population size"


class Fact(Frozen):
    fact_name: str
    published_value: Any
    graph_value: Any = None
    status: Literal["exact", "within_tolerance", "differs", "not_computable"]
    reason: str
    citation_ids: tuple[str, ...]


class DocketAnswer(Frozen):
    question_id: str
    outcome: Outcome
    headline: str
    numbers: dict[str, Any] = Field(default_factory=dict)
    citation_ids: tuple[str, ...] = ()
    coverage_ids: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


class Annotation(Frozen):
    annotation_id: str
    scanner: str
    transcript_id: str
    value: dict[str, Any]
    citation_ids: tuple[str, ...]
    validation: dict[str, Any]
    model: str
    spend_usd: float = Field(ge=0)


TABLE_MODELS: dict[str, type[Frozen]] = {
    "annotations": Annotation,
    "witnesses": Witness,
    "clocks": Clock,
    "entities": Entity,
    "time_claims": TimeClaim,
    "relations": Relation,
    "citations": Citation,
    "populations": Population,
    "coverage": CoverageEstimate,
    "capture_recapture": CaptureRecapture,
    "facts": Fact,
    "docket_answers": DocketAnswer,
}
