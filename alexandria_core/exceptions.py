"""
alexandria_core/exceptions.py — Protocol-specific exception hierarchy

Usage
-----
Replace bare ``except Exception`` with the most specific class:

    except SchemaError:      schema / field validation problems
    except ValidationError:  claim-level constraint violations
    except PersistenceError: Neo4j or storage failures
    except PatchChainError:  SHA-256 chain integrity violations
    except AuditError:       audit-gate rejections

Only the outermost pipeline layer (pipeline.py) should catch the base
``AlexandriaError`` as a fallback; all inner layers should be specific.
"""

from __future__ import annotations


class AlexandriaError(Exception):
    """Base class for all protocol errors."""


# ── Schema / structural ───────────────────────────────────────────────────────

class SchemaError(AlexandriaError):
    """A node or edge violates the Alexandria schema (missing fields, bad type)."""


class RelationAdmissibilityError(SchemaError):
    """A (Category, Predicate) pair is not in the RelationsMatrix."""


# ── Claim validation ──────────────────────────────────────────────────────────

class ValidationError(AlexandriaError):
    """A ClaimNode failed protocol-level constraint validation."""


class UncertaintyRequiredError(ValidationError):
    """An EMPIRICAL claim is missing mandatory uncertainty fields."""


class AssumptionsMissingError(ValidationError):
    """A claim's assumptions[] list is empty when it must not be."""


# ── Patch chain ───────────────────────────────────────────────────────────────

class PatchChainError(AlexandriaError):
    """Patch-chain invariant violated (hash mismatch, replay attack, etc.)."""


class IntegrityError(PatchChainError):
    """SHA-256 chain integrity check failed."""


# ── Persistence ───────────────────────────────────────────────────────────────

class PersistenceError(AlexandriaError):
    """A Neo4j or storage operation failed."""


class ConnectionError(PersistenceError):  # noqa: A001
    """Cannot connect to the Neo4j instance."""


class WriteError(PersistenceError):
    """A write transaction failed or was rolled back."""


# ── Audit ─────────────────────────────────────────────────────────────────────

class AuditError(AlexandriaError):
    """The AuditGate rejected a patch or claim."""


# ── Builder / LLM ─────────────────────────────────────────────────────────────

class BuilderError(AlexandriaError):
    """An LLM Builder call failed or returned unparseable output."""


class LLMResponseError(BuilderError):
    """The LLM returned a response that could not be parsed into ClaimNodes."""
