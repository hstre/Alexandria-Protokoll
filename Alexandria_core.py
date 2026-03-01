#!/usr/bin/env python3
"""
Alexandria Protocol — Minimal Reference Implementation (v0.1)
- Epistemic graph with typed nodes (EMPIRICAL/NORMATIVE/MODEL/SPECULATIVE)
- Patch-DSL as dict schema (ADD/MODIFY/DEPRECATE/BRANCH)
- Audit gate (structural admissibility)
- Append-only hash chain anchoring (tamper detection)
- Branch support (forks from parent patch)
- Deterministic state reconstruction from patch chains
- Simple stability update (persistence proxy, not truth probability)
This is a pedagogical reference, not production code.
SIMPLIFICATIONS (deliberate):
- Stability update uses a discrete approximation (incremental gain + linear decay)
rather than the continuous exponentially weighted integral S_k(t) defined in
Section XI.4. A production implementation would integrate v_k(τ)·e^(−λ_k(t−τ))
over the full validation history.
- λ_k (decay constant) is passed as a flat per-patch parameter ("decay") rather
than being domain-calibrated per knowledge element as specified in XI.4.
- Audit gate implements core structural checks (schema, category purity, temporal
monotonicity, uncertainty disclosure) but does not yet implement the full
five-block audit defined in Section X (path reconstruction verification,
cross-assessment verification).
- Uncertainty propagation constraint (VII.6: U(k₂) ≥ f(U(k₁))) is not enforced.
- Epistemic identity ID(k) = Hash(initial_patch, lineage) is tracked via the
lineage list but not computed as a formal identity hash.
- No persistent storage backend; all state is in-memory.
These simplifications are intentional. This implementation demonstrates protocol
constructibility and invariant preservation, not production readiness.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional, List, Tuple
import hashlib
import json
import time
# ----------------------------- Utilities -----------------------------
def sha256_json(obj: Any) -> str:
"""Stable hash of a JSON-serializable object."""
payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
return hashlib.sha256(payload.encode("utf-8")).hexdigest()
def now_unix() -> int:
return int(time.time())
def clamp01(x: float) -> float:
return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x
# ----------------------------- Data Model -----------------------------
CATEGORIES = {"EMPIRICAL", "NORMATIVE", "MODEL", "SPECULATIVE"}
OPS = {"ADD", "MODIFY", "DEPRECATE", "BRANCH"}
@dataclass(frozen=True)
class Uncertainty:
sigma: float
ci: Optional[Tuple[float, float]] = None
n: Optional[int] = None
@dataclass
class Node:
node_id: str
category: str
content: str
assumptions: List[str]
uncertainty: Optional[Uncertainty] = None
stability: float = 0.0 deprecated: bool = False
lineage: List[str] = None # structural persistence proxy
# patch_ids affecting this node
def __post_init__(self):
if self.lineage is None:
self.lineage = []
@dataclass(frozen=True)
class Patch:
patch_id: str
parent_patch_id: Optional[str]
branch_id: str
timestamp: int
operation: str
target_id: str category: str payload: Dict[str, Any] # node_id (or patch_id for BRANCH target)
# category of content claim (or MODEL for BRANCH typically
# node fields or operation details
audit: Dict[str, Any] # audit metadata (results, refs, notes)
uncertainty: Optional[Dict[str, Any]] = None
chain: Optional[Dict[str, str]] = None # commit_hash + previous_commit_hash
# ----------------------------- Audit Gate -----------------------------
class AuditError(Exception):
pass
def audit_patch(patch: Patch) -> None:
"""Structural admissibility checks (minimal but strict)."""
if patch.operation not in OPS:
raise AuditError(f"Invalid operation: {patch.operation}")
if patch.category not in CATEGORIES:
raise AuditError(f"Invalid category: {patch.category}")
if not patch.patch_id or not isinstance(patch.patch_id, str):
raise AuditError("Missing/invalid patch_id")
if not isinstance(patch.timestamp, int) or patch.timestamp <= 0:
raise AuditError("Missing/invalid timestamp")
if not patch.branch_id:
raise AuditError("Missing branch_id")
if not patch.target_id:
raise AuditError("Missing target_id")
if not isinstance(patch.payload, dict):
raise AuditError("payload must be dict")
if not isinstance(patch.audit, dict):
raise AuditError("audit must be dict")
# Category purity: payload must not smuggle category changes unless explicitly MODIFY w/
if patch.operation in {"ADD", "MODIFY"}:
if "content" not in patch.payload or not isinstance(patch.payload["content"], str):
raise AuditError("payload.content must be a string")
if "assumptions" in patch.payload and not isinstance(patch.payload["assumptions"], li
raise AuditError("payload.assumptions must be list if present")
# Uncertainty disclosure: if uncertainty provided, must include sigma
if patch.uncertainty is not None:
if "sigma" not in patch.uncertainty:
raise AuditError("uncertainty requires sigma")
if not isinstance(patch.uncertainty["sigma"], (int, float)):
raise AuditError("uncertainty.sigma must be number")
# Temporal monotonicity checked at chain integration time (needs repository context)
# ----------------------------- Core Store -----------------------------
class AlexandriaStore:
"""
Holds multiple branches.
Each branch: an append-only patch chain + derived state (graph).
"""
def __init__(self):
self.branches: Dict[str, List[Patch]] = {"main": []}
self.nodes: Dict[str, Node] = {} # derived state of current branch (when "checked ou
self.current_branch: str = "main"
self._last_commit_hash: Dict[str, Optional[str]] = {"main": None}
self._last_timestamp: Dict[str, int] = {"main": 0}
# ---------- Branch Management ----------
def checkout(self, branch_id: str) -> None:
if branch_id not in self.branches:
raise KeyError(f"Unknown branch: {branch_id}")
self.current_branch = branch_id
self.nodes = self.reconstruct(branch_id)
def create_branch(self, new_branch_id: str, from_patch_id: Optional[str] = None) -> None:
if new_branch_id in self.branches:
raise ValueError("Branch already exists")
base_chain = self.branches[self.current_branch]
if from_patch_id is None:
# branch from HEAD
self.branches[new_branch_id] = list(base_chain)
self._last_commit_hash[new_branch_id] = self._last_commit_hash[self.current_branc
self._last_timestamp[new_branch_id] = self._last_timestamp[self.current_branch]
else:
# branch from a specific patch (inclusive)
idx = next((i for i, p in enumerate(base_chain) if p.patch_id == from_patch_id),
if idx is None:
raise KeyError("from_patch_id not found in current branch")
sliced = base_chain[: idx + 1]
self.branches[new_branch_id] = list(sliced)
self._last_commit_hash[new_branch_id] = sliced[-1].chain["commit_hash"] if self._last_timestamp[new_branch_id] = sliced[-1].timestamp
# no auto-checkout
sliced
# ---------- Patch Submission ----------
def submit(self, patch: Patch) -> str:
"""Audit + chain-anchor + append + apply."""
audit_patch(patch)
b = patch.branch_id
if b not in self.branches:
raise KeyError(f"Branch does not exist: {b}")
# temporal monotonicity within a branch
if patch.timestamp <= self._last_timestamp[b]:
raise AuditError(f"Non-monotonic timestamp for branch {b}: {patch.timestamp} <= {
# parent linkage check
chain = self.branches[b]
expected_parent = chain[-1].patch_id if chain else None
if patch.parent_patch_id != expected_parent:
raise AuditError(f"parent_patch_id mismatch: got {patch.parent_patch_id}, expecte
# chain anchor
prev = self._last_commit_hash[b]
patch_dict = asdict(patch)
patch_dict["chain"] = {"previous_commit_hash": prev, "commit_hash": None}
commit_hash = sha256_json(patch_dict) # includes previous hash
patch_dict["chain"]["commit_hash"] = commit_hash
anchored = Patch(**{k: patch_dict[k] for k in patch_dict if k in Patch.__annotations_
self.branches[b].append(anchored)
self._last_commit_hash[b] = commit_hash
self._last_timestamp[b] = patch.timestamp
# If currently checked out, apply incrementally
if self.current_branch == b:
self.apply_patch_in_place(anchored)
return commit_hash
# ---------- State Evolution ----------
def apply_patch_in_place(self, patch: Patch) -> None:
op = patch.operation
tid = patch.target_id
if op == "BRANCH":
return
if op == "ADD":
if tid in self.nodes and not self.nodes[tid].deprecated:
raise AuditError(f"ADD to existing active node: {tid}")
self.nodes[tid] = self._node_from_patch(patch, existing=None)
return
if op == "MODIFY":
if tid not in self.nodes:
raise AuditError(f"MODIFY unknown node: {tid}")
self.nodes[tid] = self._node_from_patch(patch, existing=self.nodes[tid])
return
if op == "DEPRECATE":
if tid not in self.nodes:
raise AuditError(f"DEPRECATE unknown node: {tid}")
n = self.nodes[tid]
n.deprecated = True
n.lineage.append(patch.patch_id)
n.stability = clamp01(n.stability * 0.25)
return
raise AuditError(f"Unhandled op: {op}")
def _node_from_patch(self, patch: Patch, existing: Optional[Node]) -> Node:
p = patch.payload
content = p.get("content", existing.content if existing else "")
assumptions = p.get("assumptions", existing.assumptions if existing else [])
category = patch.category if patch.operation == "ADD" else (p.get("category", existin
# Uncertainty
unc = None
if patch.uncertainty is not None:
unc = Uncertainty(
sigma=float(patch.uncertainty.get("sigma")),
ci=tuple(patch.uncertainty["ci"]) if "ci" in patch.uncertainty else None,
n=int(patch.uncertainty["n"]) if "n" in patch.uncertainty else None,
)
else:
unc = existing.uncertainty if existing else None
# Stability update (proxy)
validated = bool(patch.audit.get("validated", True))
decay = float(patch.audit.get("decay", 0.01))
prev_stability = existing.stability if existing else 0.0
new_stability = self._update_stability(prev_stability, validated=validated, decay=dec
n = Node(
node_id=patch.target_id,
category=category,
content=content,
assumptions=list(assumptions),
uncertainty=unc,
stability=new_stability,
deprecated=existing.deprecated if existing else False,
lineage=list(existing.lineage) if existing else [],
)
n.lineage.append(patch.patch_id)
return n
@staticmethod
def _update_stability(prev: float, validated: bool, decay: float) -> float:
prev = clamp01(prev)
if validated:
prev = prev + (1.0 - prev) * 0.10 # approach 1 slowly
prev = prev * (1.0 - max(0.0, min(decay, 1.0)))
return clamp01(prev)
# ---------- Reconstruction & Verification ----------
def reconstruct(self, branch_id: str) -> Dict[str, Node]:
"""Rebuild graph state from patch chain with full integrity verification."""
if branch_id not in self.branches:
raise KeyError(f"Unknown branch: {branch_id}")
prev_hash = None
last_ts = 0
nodes: Dict[str, Node] = {}
for patch in self.branches[branch_id]:
if patch.timestamp <= last_ts:
raise AuditError("Non-monotonic timestamp detected during reconstruction")
last_ts = patch.timestamp
# verify chain hashes
patch_dict = asdict(patch)
commit = patch.chain["commit_hash"] if patch.chain else None
expected_prev = patch.chain["previous_commit_hash"] if patch.chain else None
if expected_prev != prev_hash:
raise AuditError("Hash-chain discontinuity detected")
patch_dict["chain"]["commit_hash"] = None
recomputed = sha256_json(patch_dict)
if commit is None or commit != recomputed:
raise AuditError("Tamper detected: commit hash mismatch")
prev_hash = commit
# apply to nodes
op = patch.operation
tid = patch.target_id
if op == "BRANCH":
continue
if op == "ADD":
nodes[tid] = self._node_from_patch(patch, existing=None)
elif op == "MODIFY":
if tid not in nodes:
raise AuditError(f"MODIFY unknown node during reconstruction: {tid}")
nodes[tid] = self._node_from_patch(patch, existing=nodes[tid])
elif op == "DEPRECATE":
if tid not in nodes:
raise AuditError(f"DEPRECATE unknown node during reconstruction: {tid}")
nodes[tid].deprecated = True
nodes[tid].lineage.append(patch.patch_id)
nodes[tid].stability = clamp01(nodes[tid].stability * 0.25)
return nodes
# ---------- Convenience API ----------
def get_node(self, node_id: str) -> Node:
return self.nodes[node_id]
def list_nodes(self) -> List[str]:
return sorted(self.nodes.keys())
def status_report(self) -> Dict[str, Any]:
"""Minimal epistemic status report for current branch."""
report = {"branch": self.current_branch, "nodes": []}
for nid in self.list_nodes():
n = self.nodes[nid]
report["nodes"].append({
"id": n.node_id,
"category": n.category,
"deprecated": n.deprecated,
"stability": round(n.stability, 4),
"sigma": (round(n.uncertainty.sigma, 6) if n.uncertainty else None),
"assumptions": n.assumptions,
"lineage_len": len(n.lineage),
})
return report
# ----------------------------- Demo -----------------------------
def demo():
store = AlexandriaStore()
store.checkout("main")
# ADD claim
p1 = Patch(
patch_id="patch_001",
parent_patch_id=None,
branch_id="main",
timestamp=1771459200,
operation="ADD",
target_id="claim_001",
category="EMPIRICAL",
payload={
"content": "Material X increases energy density by 20% under stated conditions.",
"assumptions": ["Temp_18C_25C", "Pressure_1atm", "Measurement_Calibrated_v1"],
},
audit={"validated": True, "decay": 0.01},
uncertainty={"sigma": 0.04, "ci": [0.16, 0.24], "n": 1200},
chain=None,
)
store.submit(p1)
# BRANCH event
store.create_branch("b_temp_constraint")
store.checkout("b_temp_constraint")
# MODIFY claim in branch
p2 = Patch(
patch_id="patch_002",
parent_patch_id="patch_001",
branch_id="b_temp_constraint",
timestamp=1771465000,
operation="MODIFY",
target_id="claim_001",
category="EMPIRICAL",
payload={
"content": "Material X increases energy density by 20% only below 0C; unstable at
"assumptions": ["Temp_below_0C", "Measurement_Calibrated_v1"],
},
audit={"validated": True, "decay": 0.01},
uncertainty={"sigma": 0.11, "ci": [-0.02, 0.21], "n": 5000},
chain=None,
)
store.submit(p2)
# Deprecate in branch
p3 = Patch(
patch_id="patch_003",
parent_patch_id="patch_002",
branch_id="b_temp_constraint",
timestamp=1785000200,
operation="DEPRECATE",
target_id="claim_001",
category="EMPIRICAL",
payload={"content": "Deprecated due to calibration drift artifact."},
audit={"validated": True, "decay": 0.0},
uncertainty=None,
chain=None,
)
store.submit(p3)
# Print status
print(json.dumps(store.status_report(), indent=2, ensure_ascii=False))
# Reconstruct and verify tamper detection
reconstructed = store.reconstruct("b_temp_constraint")
assert reconstructed["claim_001"].deprecated is True
print("Reconstruction OK; tamper detection OK.")
if __name__ == "__main__":
demo()
