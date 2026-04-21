"""
tests/test_evaluator_protocol.py -- Ticket B1-b unit coverage
=============================================================
Covers:
  - PrimaryEvaluator protocol runtime isinstance check.
  - EvaluationOutput construction with all extended EvidenceKind values.
  - EvaluationOutput rejects unknown evidence kind with ValueError.
  - Canonicalizer registry dispatches "companyos-verdict/0.2" correctly.
  - StubPassthroughEvaluator satisfies the protocol and returns canned output.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.primitives.canonicalizer_registry import default_canonicalizer_registry
from core.primitives.evaluator import EvaluationOutput, PrimaryEvaluator
from core.primitives.oracle import _VALID_EVIDENCE_KINDS
from tests.fixtures.evaluators import StubPassthroughEvaluator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _output(kind: str, score: str = "0.95") -> EvaluationOutput:
    """Build a minimal valid EvaluationOutput for a given evidence kind."""
    return EvaluationOutput(
        result="accepted",
        score=Decimal(score),
        evidence={"kind": kind},
        evaluator_canonical_hash="deadbeef",
    )


# ---------------------------------------------------------------------------
# PrimaryEvaluator protocol -- runtime isinstance
# ---------------------------------------------------------------------------
class TestPrimaryEvaluatorProtocol:
    def test_stub_satisfies_protocol_isinstance(self) -> None:
        stub = StubPassthroughEvaluator(
            evaluator_did="did:companyos:stub",
            canonical_hash="hash-stub",
            canned_output=_output("schema_pass"),
        )
        assert isinstance(stub, PrimaryEvaluator)

    def test_class_missing_evaluator_did_does_not_satisfy_protocol(self) -> None:
        class NoDid:
            @property
            def canonical_hash(self) -> str:
                return "x"

            def evaluate(self, sla, artifact_bytes, *, artifact_properties=None):
                pass

        assert not isinstance(NoDid(), PrimaryEvaluator)

    def test_class_missing_canonical_hash_does_not_satisfy_protocol(self) -> None:
        class NoHash:
            @property
            def evaluator_did(self) -> str:
                return "did:x"

            def evaluate(self, sla, artifact_bytes, *, artifact_properties=None):
                pass

        assert not isinstance(NoHash(), PrimaryEvaluator)

    def test_class_missing_evaluate_does_not_satisfy_protocol(self) -> None:
        class NoEvaluate:
            @property
            def evaluator_did(self) -> str:
                return "did:x"

            @property
            def canonical_hash(self) -> str:
                return "x"

        assert not isinstance(NoEvaluate(), PrimaryEvaluator)


# ---------------------------------------------------------------------------
# Extended EvidenceKind -- all 12 kinds are valid on EvaluationOutput
# ---------------------------------------------------------------------------
class TestExtendedEvidenceKinds:
    # v1a kinds (9 original)
    @pytest.mark.parametrize("kind", [
        "schema_pass",
        "schema_fail",
        "hash_mismatch",
        "artifact_parse_error",
        "sla_schema_malformed",
        "sla_missing_schema",
        "unsupported_schema_kind",
        "unsupported_schema_version",
        "founder_override",
    ])
    def test_v1a_kind_accepted(self, kind: str) -> None:
        out = _output(kind)
        assert out.evidence["kind"] == kind

    # v1b additions (3 new)
    @pytest.mark.parametrize("kind", [
        "evaluator_error",
        "evaluator_timeout",
        "schema_pass_with_score",
    ])
    def test_v1b_kind_accepted(self, kind: str) -> None:
        out = _output(kind)
        assert out.evidence["kind"] == kind

    def test_all_12_kinds_present_in_valid_set(self) -> None:
        """_VALID_EVIDENCE_KINDS must contain exactly 12 entries after v1b."""
        # Enumerated defensively so a missed addition is caught.
        expected = {
            "schema_pass",
            "schema_fail",
            "hash_mismatch",
            "artifact_parse_error",
            "sla_schema_malformed",
            "sla_missing_schema",
            "unsupported_schema_kind",
            "unsupported_schema_version",
            "founder_override",
            "evaluator_error",
            "evaluator_timeout",
            "schema_pass_with_score",
        }
        assert _VALID_EVIDENCE_KINDS == expected

    def test_unknown_kind_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="unknown kind"):
            _output("totally_unknown_kind")

    def test_missing_kind_key_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="unknown kind"):
            EvaluationOutput(
                result="accepted",
                score=Decimal("1"),
                evidence={"no_kind_key": True},
                evaluator_canonical_hash="abc",
            )


# ---------------------------------------------------------------------------
# EvaluationOutput frozen dataclass properties
# ---------------------------------------------------------------------------
class TestEvaluationOutput:
    def test_frozen_rejects_field_mutation(self) -> None:
        out = _output("schema_pass")
        with pytest.raises((AttributeError, TypeError)):
            out.result = "rejected"  # type: ignore[misc]

    def test_result_accepted(self) -> None:
        out = _output("schema_pass")
        assert out.result == "accepted"

    def test_result_rejected(self) -> None:
        out = EvaluationOutput(
            result="rejected",
            score=Decimal("0.1"),
            evidence={"kind": "schema_fail"},
            evaluator_canonical_hash="abc",
        )
        assert out.result == "rejected"

    def test_result_refunded(self) -> None:
        out = EvaluationOutput(
            result="refunded",
            score=Decimal("0"),
            evidence={"kind": "evaluator_timeout"},
            evaluator_canonical_hash="abc",
        )
        assert out.result == "refunded"

    def test_score_preserved_as_decimal(self) -> None:
        out = _output("schema_pass_with_score", score="0.7654321")
        assert out.score == Decimal("0.7654321")

    def test_evaluator_canonical_hash_preserved(self) -> None:
        out = _output("schema_pass")
        assert out.evaluator_canonical_hash == "deadbeef"


# ---------------------------------------------------------------------------
# Canonicalizer registry -- v0.2 dispatch
# ---------------------------------------------------------------------------
# The default_canonicalizer_registry is a process-global singleton. An earlier
# test in test_canonicalizer_registry.py registers a stub 0.2 canonicalizer
# and then removes 0.2 entirely in its teardown. When the full suite runs,
# this teardown can fire BEFORE our tests here, leaving 0.2 unregistered.
#
# We fix this by using a module-scoped fixture that re-registers the real
# oracle.py 0.2 canonicalizer before any test in this class touches the
# registry. This is not test pollution -- it replicates the exact registration
# that oracle.py performs at module-load time; we're just restoring it after
# the other test's aggressive teardown.
import pytest as _pytest


@_pytest.fixture(autouse=False)
def ensure_v02_registered():
    """Ensure companyos-verdict/0.2 is registered on the default registry.

    Re-registers the real oracle.py canonicalizer in case a prior test's
    teardown removed it from the singleton. Leaves the registry in the
    same state as a fresh process after oracle.py is imported.
    """
    from core.primitives.oracle import _canonical_bytes as _real_cb
    default_canonicalizer_registry.register("companyos-verdict/0.2", _real_cb)
    yield


class TestCanonicalizerV02:
    def test_v02_registered_in_default_registry(self, ensure_v02_registered) -> None:
        """companyos-verdict/0.2 must be registered after oracle.py loads."""
        fn = default_canonicalizer_registry.get("companyos-verdict/0.2")
        assert callable(fn)

    def test_v02_produces_bytes(self, ensure_v02_registered) -> None:
        fn = default_canonicalizer_registry.get("companyos-verdict/0.2")
        shell = {
            "sla_id": "test-sla",
            "protocol_version": "companyos-verdict/0.2",
            "result": "accepted",
        }
        result = fn(shell, exclude_verdict_hash=False)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_v02_bytes_match_v01_bytes(self, ensure_v02_registered) -> None:
        """v0.2 uses identical byte rules to v0.1 for now (per Decision 5)."""
        fn_01 = default_canonicalizer_registry.get("companyos-verdict/0.1")
        fn_02 = default_canonicalizer_registry.get("companyos-verdict/0.2")
        shell = {
            "sla_id": "test-sla",
            "artifact_hash": "deadbeef",
            "tier": 1,
            "result": "accepted",
            "evaluator_did": "did:companyos:eval",
            "evidence": {"kind": "schema_pass_with_score"},
            "verdict_hash": "aabbcc",
            "signer": {"bytes_hex": "aa" * 32},
            "issued_at": "2026-04-21T00:00:00Z",
            "score": None,
        }
        bytes_01 = fn_01(shell, exclude_verdict_hash=False)
        bytes_02 = fn_02(shell, exclude_verdict_hash=False)
        assert bytes_01 == bytes_02

    def test_v02_exclude_verdict_hash_flag_respected(self, ensure_v02_registered) -> None:
        fn = default_canonicalizer_registry.get("companyos-verdict/0.2")
        shell = {
            "sla_id": "sla-x",
            "verdict_hash": "abc123",
            "protocol_version": "companyos-verdict/0.2",
        }
        with_hash = fn(shell, exclude_verdict_hash=False)
        without_hash = fn(shell, exclude_verdict_hash=True)
        assert b"abc123" in with_hash
        assert b"abc123" not in without_hash


# ---------------------------------------------------------------------------
# StubPassthroughEvaluator -- canned output contract
# ---------------------------------------------------------------------------
class TestStubPassthroughEvaluator:
    def test_evaluate_returns_canned_output(self) -> None:
        canned = _output("schema_pass_with_score")
        stub = StubPassthroughEvaluator(
            evaluator_did="did:companyos:stub-eval",
            canonical_hash="stub-hash",
            canned_output=canned,
        )
        # evaluate() should return the canned output regardless of inputs.
        result = stub.evaluate(None, b"any bytes", artifact_properties=None)  # type: ignore[arg-type]
        assert result is canned

    def test_evaluator_did_property(self) -> None:
        canned = _output("schema_pass")
        stub = StubPassthroughEvaluator("did:x:y", "hash-abc", canned)
        assert stub.evaluator_did == "did:x:y"

    def test_canonical_hash_property(self) -> None:
        canned = _output("schema_pass")
        stub = StubPassthroughEvaluator("did:x", "canonical-xyz", canned)
        assert stub.canonical_hash == "canonical-xyz"

    def test_canned_output_ignored_inputs(self) -> None:
        """Different input args should still return the same canned output."""
        canned = _output("evaluator_error")
        stub = StubPassthroughEvaluator("did:test", "hash", canned)
        assert stub.evaluate(None, b"")  is canned  # type: ignore[arg-type]
        assert stub.evaluate(None, b"different bytes") is canned  # type: ignore[arg-type]
        assert stub.evaluate(None, b"x", artifact_properties={"k": "v"}) is canned  # type: ignore[arg-type]
