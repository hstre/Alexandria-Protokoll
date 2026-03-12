"""
Alexandria Core — db.py
Neo4j connection, schema constraints, and CRUD operations.

Requirements:
    pip install neo4j

Neo4j connection defaults:
    URI:      bolt://localhost:7687
    User:     neo4j
    Password: set via env var ALEXANDRIA_NEO4J_PASSWORD
              or passed directly to AlexandriaDB()
"""

from __future__ import annotations
import os
import json
import logging
from typing import Optional, Any

from neo4j import GraphDatabase, Driver, Session
from neo4j.exceptions import ServiceUnavailable, AuthError

from .schema import (
    ClaimNode, EntityNode, ConceptNode, WorkNode, AuthorNode,
    InstitutionNode, EvidenceNode, JudgmentNode, Patch,
    EpistemicStatus, PatchOperation, Category,
)

log = logging.getLogger(__name__)


# ── Schema constraints and indexes ────────────────────────────────────────────

CONSTRAINTS = [
    # Uniqueness constraints (one per node label + ID property)
    "CREATE CONSTRAINT claim_id_unique IF NOT EXISTS "
    "FOR (n:Claim) REQUIRE n.claim_id IS UNIQUE",

    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS "
    "FOR (n:Entity) REQUIRE n.entity_id IS UNIQUE",

    "CREATE CONSTRAINT concept_id_unique IF NOT EXISTS "
    "FOR (n:Concept) REQUIRE n.concept_id IS UNIQUE",

    "CREATE CONSTRAINT work_id_unique IF NOT EXISTS "
    "FOR (n:Work) REQUIRE n.work_id IS UNIQUE",

    "CREATE CONSTRAINT author_id_unique IF NOT EXISTS "
    "FOR (n:Author) REQUIRE n.author_id IS UNIQUE",

    "CREATE CONSTRAINT institution_id_unique IF NOT EXISTS "
    "FOR (n:Institution) REQUIRE n.institution_id IS UNIQUE",

    "CREATE CONSTRAINT evidence_id_unique IF NOT EXISTS "
    "FOR (n:Evidence) REQUIRE n.evidence_id IS UNIQUE",

    "CREATE CONSTRAINT judgment_id_unique IF NOT EXISTS "
    "FOR (n:Judgment) REQUIRE n.judgment_id IS UNIQUE",

    "CREATE CONSTRAINT patch_id_unique IF NOT EXISTS "
    "FOR (n:Patch) REQUIRE n.patch_id IS UNIQUE",

    "CREATE CONSTRAINT branch_id_unique IF NOT EXISTS "
    "FOR (n:Branch) REQUIRE n.branch_id IS UNIQUE",
]

INDEXES = [
    # Status index — frequent query pattern
    "CREATE INDEX claim_status IF NOT EXISTS "
    "FOR (n:Claim) ON (n.status)",

    # Category index
    "CREATE INDEX claim_category IF NOT EXISTS "
    "FOR (n:Claim) ON (n.category)",

    # Builder origin index (DBA)
    "CREATE INDEX claim_builder IF NOT EXISTS "
    "FOR (n:Claim) ON (n.builder_origin)",

    # Work DOI index
    "CREATE INDEX work_doi IF NOT EXISTS "
    "FOR (n:Work) ON (n.doi)",

    # Work OpenAlex ID
    "CREATE INDEX work_openalex IF NOT EXISTS "
    "FOR (n:Work) ON (n.openalex_id)",

    # Patch timestamp index — for temporal queries
    "CREATE INDEX patch_timestamp IF NOT EXISTS "
    "FOR (n:Patch) ON (n.timestamp)",
]


# ── Database class ─────────────────────────────────────────────────────────────

class AlexandriaDB:
    """
    Neo4j interface for the Alexandria graph.

    Usage:
        db = AlexandriaDB()
        db.connect()
        db.deploy_schema()

        claim = ClaimNode.new(...)
        db.upsert_claim(claim)

        db.close()

    Or as context manager:
        with AlexandriaDB() as db:
            db.deploy_schema()
            db.upsert_claim(claim)
    """

    def __init__(
        self,
        uri:      str = "bolt://localhost:7687",
        user:     str = "neo4j",
        password: str | None = None,
    ):
        self.uri      = uri
        self.user     = user
        self.password = password or os.environ.get("ALEXANDRIA_NEO4J_PASSWORD", "")
        self._driver: Optional[Driver] = None

    def connect(self) -> "AlexandriaDB":
        """Open driver. Raises on auth/connection failure."""
        try:
            self._driver = GraphDatabase.driver(
                self.uri, auth=(self.user, self.password)
            )
            self._driver.verify_connectivity()
            log.info(f"Connected to Neo4j at {self.uri}")
        except ServiceUnavailable as e:
            raise ConnectionError(
                f"Neo4j not reachable at {self.uri}. "
                "Start Neo4j Desktop or: docker run -p7687:7687 -p7474:7474 "
                "-e NEO4J_AUTH=neo4j/password neo4j:5"
            ) from e
        except AuthError as e:
            raise PermissionError(
                "Neo4j authentication failed. Check ALEXANDRIA_NEO4J_PASSWORD."
            ) from e
        return self

    def close(self):
        if self._driver:
            self._driver.close()
            self._driver = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.close()

    def _session(self) -> Session:
        if not self._driver:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._driver.session()

    # ── Schema deployment ─────────────────────────────────────────────────────

    def deploy_schema(self):
        """
        Create constraints and indexes.
        Safe to call multiple times (IF NOT EXISTS guards).
        """
        with self._session() as s:
            for stmt in CONSTRAINTS:
                s.run(stmt)
            for stmt in INDEXES:
                s.run(stmt)
        log.info("Schema deployed (constraints + indexes).")

    def drop_all(self):
        """
        Delete all nodes and relationships.
        USE ONLY IN TESTS / DEVELOPMENT.
        """
        with self._session() as s:
            s.run("MATCH (n) DETACH DELETE n")
        log.warning("All nodes and relationships deleted.")

    # ── Claim CRUD ────────────────────────────────────────────────────────────

    def upsert_claim(self, claim: ClaimNode) -> None:
        """
        Insert or update a Claim node.
        Properties are serialized; nested dicts stored as JSON strings.
        """
        errors = claim.validate()
        if errors:
            raise ValueError(f"Claim validation failed: {errors}")

        d = claim.to_dict()
        # Serialize nested objects to JSON strings for Neo4j compatibility
        for key in ("qualifiers", "scope", "time_scope", "uncertainty", "validation"):
            if key in d and d[key]:
                d[key] = json.dumps(d[key])
            else:
                d[key] = ""
        d["assumptions"]   = d["assumptions"]   # list → stored as list
        d["evidence_refs"] = d["evidence_refs"]
        d["source_refs"]   = d["source_refs"]
        d["lineage"]       = d["lineage"]

        cypher = """
        MERGE (c:Claim {claim_id: $claim_id})
        SET c += $props
        """
        with self._session() as s:
            s.run(cypher, claim_id=claim.claim_id, props=d)

    def get_claim(self, claim_id: str) -> Optional[ClaimNode]:
        """Fetch a Claim by ID. Returns None if not found."""
        with self._session() as s:
            result = s.run(
                "MATCH (c:Claim {claim_id: $cid}) RETURN c", cid=claim_id
            )
            record = result.single()
            if record is None:
                return None
            return self._claim_from_record(dict(record["c"]))

    def get_claims_by_status(self, status: EpistemicStatus) -> list[ClaimNode]:
        """Fetch all Claims with a given protocol status."""
        with self._session() as s:
            result = s.run(
                "MATCH (c:Claim {status: $status}) RETURN c",
                status=status.value,
            )
            return [self._claim_from_record(dict(r["c"])) for r in result]

    def get_claims_by_builder(self, builder_origin: str) -> list[ClaimNode]:
        """Fetch all Claims from a specific builder (alpha | beta | adjudicated)."""
        with self._session() as s:
            result = s.run(
                "MATCH (c:Claim {builder_origin: $bo}) RETURN c",
                bo=builder_origin,
            )
            return [self._claim_from_record(dict(r["c"])) for r in result]

    def update_claim_status(self, claim_id: str, status: EpistemicStatus) -> None:
        """Update only the status field of a Claim."""
        with self._session() as s:
            s.run(
                "MATCH (c:Claim {claim_id: $cid}) SET c.status = $status",
                cid=claim_id, status=status.value,
            )

    def _claim_from_record(self, props: dict) -> ClaimNode:
        """Deserialize Neo4j properties back into ClaimNode."""
        for key in ("qualifiers", "scope", "time_scope", "uncertainty", "validation"):
            if props.get(key):
                try:
                    props[key] = json.loads(props[key])
                except (json.JSONDecodeError, TypeError):
                    props[key] = {}
        return ClaimNode.from_dict(props)

    # ── Entity CRUD ───────────────────────────────────────────────────────────

    def upsert_entity(self, entity: EntityNode) -> None:
        with self._session() as s:
            s.run(
                "MERGE (e:Entity {entity_id: $eid}) SET e += $props",
                eid=entity.entity_id, props=entity.to_dict(),
            )

    def get_entity(self, entity_id: str) -> Optional[EntityNode]:
        with self._session() as s:
            result = s.run(
                "MATCH (e:Entity {entity_id: $eid}) RETURN e", eid=entity_id
            )
            r = result.single()
            if r is None:
                return None
            d = dict(r["e"])
            return EntityNode(**d)

    # ── Work CRUD ─────────────────────────────────────────────────────────────

    def upsert_work(self, work: WorkNode) -> None:
        with self._session() as s:
            s.run(
                "MERGE (w:Work {work_id: $wid}) SET w += $props",
                wid=work.work_id, props=work.to_dict(),
            )

    def get_work_by_doi(self, doi: str) -> Optional[WorkNode]:
        with self._session() as s:
            result = s.run("MATCH (w:Work {doi: $doi}) RETURN w", doi=doi)
            r = result.single()
            if r is None:
                return None
            d = dict(r["w"])
            return WorkNode(**d)

    # ── Author / Institution ──────────────────────────────────────────────────

    def upsert_author(self, author: AuthorNode) -> None:
        with self._session() as s:
            s.run(
                "MERGE (a:Author {author_id: $aid}) SET a += $props",
                aid=author.author_id, props=author.to_dict(),
            )

    def upsert_institution(self, inst: InstitutionNode) -> None:
        with self._session() as s:
            s.run(
                "MERGE (i:Institution {institution_id: $iid}) SET i += $props",
                iid=inst.institution_id, props=inst.to_dict(),
            )

    # ── Evidence ──────────────────────────────────────────────────────────────

    def upsert_evidence(self, ev: EvidenceNode) -> None:
        d = ev.to_dict()
        if d.get("uncertainty"):
            d["uncertainty"] = json.dumps(d["uncertainty"])
        with self._session() as s:
            s.run(
                "MERGE (e:Evidence {evidence_id: $eid}) SET e += $props",
                eid=ev.evidence_id, props=d,
            )

    # ── Judgment ──────────────────────────────────────────────────────────────

    def upsert_judgment(self, j: JudgmentNode) -> None:
        with self._session() as s:
            s.run(
                "MERGE (j:Judgment {judgment_id: $jid}) SET j += $props",
                jid=j.judgment_id, props=j.to_dict(),
            )

    # ── Patch ─────────────────────────────────────────────────────────────────

    def store_patch(self, patch: Patch) -> None:
        """Store a Patch node. content is serialized to JSON."""
        d = patch.to_dict()
        d["content"] = json.dumps(d["content"])
        with self._session() as s:
            s.run(
                "MERGE (p:Patch {patch_id: $pid}) SET p += $props",
                pid=patch.patch_id, props=d,
            )

    def upsert_branch(self, branch: "BranchNode") -> None:
        """
        Persist a BranchNode as a first-class graph object (v2.2 Sprint 1).

        BranchNode carries SPL structural context (matrix_version,
        matrix_seal_hash) from WP2 when available.
        """
        from .schema import BranchNode as _BranchNode
        props = branch.to_dict()

        # SPL structural context (WP2 integration)
        if hasattr(branch, "structural_context") and branch.structural_context:
            props["matrix_version"]   = branch.structural_context.get("matrix_version", "genesis")
            props["matrix_seal_hash"] = branch.structural_context.get("matrix_seal_hash", "genesis")
        else:
            props.setdefault("matrix_version",   "genesis")
            props.setdefault("matrix_seal_hash", "genesis")

        with self._session() as s:
            s.run(
                """
                MERGE (b:Branch {branch_id: $branch_id})
                SET b.status            = $status,
                    b.created_at        = $created_at,
                    b.merge_policy      = $merge_policy,
                    b.branch_reason     = $branch_reason,
                    b.matrix_version    = $matrix_version,
                    b.matrix_seal_hash  = $matrix_seal_hash
                """,
                **props,
            )

    def get_patch_chain(self, from_patch_id: str | None = None) -> list[dict]:
        """
        Return all patches in chronological order from genesis.
        If from_patch_id given, start from that patch.
        """
        with self._session() as s:
            result = s.run(
                "MATCH (p:Patch) RETURN p ORDER BY p.timestamp ASC"
            )
            patches = [dict(r["p"]) for r in result]
            for p in patches:
                if p.get("content"):
                    try:
                        p["content"] = json.loads(p["content"])
                    except Exception:
                        pass
            if from_patch_id:
                # Find index and slice
                ids = [p["patch_id"] for p in patches]
                try:
                    idx = ids.index(from_patch_id)
                    patches = patches[idx:]
                except ValueError:
                    pass
            return patches

    # ── Relations ─────────────────────────────────────────────────────────────

    def create_relation(
        self,
        from_label: str, from_id_prop: str, from_id: str,
        to_label:   str, to_id_prop:   str, to_id:   str,
        rel_type:   str,
        props:      dict | None = None,
    ) -> None:
        """
        Create a typed relation between two nodes.
        Both nodes must already exist.
        """
        props = props or {}
        cypher = (
            f"MATCH (a:{from_label} {{{from_id_prop}: $fid}}) "
            f"MATCH (b:{to_label}   {{{to_id_prop}:   $tid}}) "
            f"MERGE (a)-[r:{rel_type}]->(b) "
            f"SET r += $props"
        )
        with self._session() as s:
            s.run(cypher, fid=from_id, tid=to_id, props=props)

    def link_claim_to_evidence(self, claim_id: str, evidence_id: str) -> None:
        self.create_relation(
            "Claim", "claim_id", claim_id,
            "Evidence", "evidence_id", evidence_id,
            "HAS_EVIDENCE",
        )

    def link_claim_to_work(self, claim_id: str, work_id: str,
                            rel_type: str = "SUPPORTED_BY") -> None:
        self.create_relation(
            "Claim", "claim_id", claim_id,
            "Work", "work_id", work_id,
            rel_type,
        )

    def link_claim_to_claim(self, from_id: str, to_id: str,
                             rel_type: str, props: dict | None = None) -> None:
        self.create_relation(
            "Claim", "claim_id", from_id,
            "Claim", "claim_id", to_id,
            rel_type, props,
        )

    def link_judgment_to_claims(
        self, judgment_id: str, claim_alpha_id: str, claim_beta_id: str
    ) -> None:
        self.create_relation(
            "Judgment", "judgment_id", judgment_id,
            "Claim", "claim_id", claim_alpha_id,
            "ADJUDICATES",
        )
        self.create_relation(
            "Judgment", "judgment_id", judgment_id,
            "Claim", "claim_id", claim_beta_id,
            "ADJUDICATES",
        )

    # ── Query helpers ─────────────────────────────────────────────────────────

    def count_nodes(self, label: str) -> int:
        with self._session() as s:
            result = s.run(f"MATCH (n:{label}) RETURN count(n) AS c")
            return result.single()["c"]

    def graph_summary(self) -> dict:
        """Return node and relation counts for all known labels."""
        labels = ["Claim", "Entity", "Concept", "Work", "Author",
                  "Institution", "Evidence", "Judgment", "Patch"]
        summary = {}
        with self._session() as s:
            for label in labels:
                r = s.run(f"MATCH (n:{label}) RETURN count(n) AS c")
                summary[label] = r.single()["c"]
            r = s.run("MATCH ()-[r]->() RETURN count(r) AS c")
            summary["_relations"] = r.single()["c"]
        return summary

    def run_cypher(self, cypher: str, **params) -> list[dict]:
        """
        Execute arbitrary Cypher. Returns list of record dicts.
        Use for custom queries not covered by the CRUD methods.
        """
        with self._session() as s:
            result = s.run(cypher, **params)
            return [dict(r) for r in result]
