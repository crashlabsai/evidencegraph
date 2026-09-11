from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from evidencegraph.ids import entity_id, relation_id
from evidencegraph.schema import (
    Citation,
    Entity,
    EntityKind,
    Frozen,
    Outcome,
    Relation,
    RelationKind,
    TrustDomainRelation,
    Witness,
)

AdapterOutput = Iterator[tuple[str, Frozen]]


class Adapter(Protocol):
    def describe(self, path: Path) -> dict: ...
    def ingest(self, path: Path, witness: Witness) -> AdapterOutput: ...


class Builder:
    def __init__(self, witness: Witness):
        self.witness = witness
        self.seen: set[str] = set()
        self.rows: list[tuple[str, Frozen]] = []

    def cite(self, ref: Citation) -> None:
        if ref.citation_id not in self.seen:
            self.rows.append(("citations", ref))
            self.seen.add(ref.citation_id)

    def entity(
        self,
        kind: EntityKind,
        subkind: str,
        key: str,
        ref: Citation,
        attrs: dict | None = None,
        **time,
    ) -> Entity:
        identifier = entity_id(subkind, self.witness.witness_id, key)
        entity = Entity(
            entity_id=identifier,
            kind=kind,
            subkind=subkind,
            natural_key=key,
            witness_id=self.witness.witness_id,
            citation_id=ref.citation_id,
            attrs=attrs or {},
            **time,
        )
        if identifier not in self.seen:
            self.cite(ref)
            self.rows.append(("entities", entity))
            self.seen.add(identifier)
        return entity

    def edge(
        self,
        kind: RelationKind,
        subject: Entity,
        obj: Entity,
        ref: Citation,
        *,
        method: str = "source_field",
        rationale: str = "Published source assertion; not actor authentication",
        outcome: Outcome = Outcome.SUPPORTED,
    ) -> None:
        identifier = relation_id(kind, subject.entity_id, obj.entity_id, method)
        if identifier in self.seen:
            return
        self.cite(ref)
        self.rows.append(
            (
                "relations",
                Relation(
                    relation_id=identifier,
                    kind=kind,
                    subject_id=subject.entity_id,
                    object_id=obj.entity_id,
                    outcome=outcome,
                    method=method,
                    rationale=rationale,
                    witness_ids=(self.witness.witness_id,),
                    citation_ids=(ref.citation_id,),
                    candidates=(obj.entity_id,)
                    if kind in {"produced", "executed"} and outcome == Outcome.SUPPORTED
                    else (),
                    trust_domain_relation=TrustDomainRelation.SAME,
                ),
            )
        )
        self.seen.add(identifier)

    def drain(self) -> list[tuple[str, Frozen]]:
        rows, self.rows = self.rows, []
        return rows
