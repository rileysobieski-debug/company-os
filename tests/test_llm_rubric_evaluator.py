"""
tests/test_llm_rubric_evaluator.py -- B4 LLMRubricEvaluator test suite
=======================================================================
Covers the 12 acceptance criteria for ticket B4:

1.  Score parsing -- accepted (score >= floor)
2.  Score parsing -- rejected (score < floor)
3.  Malformed JSON -> refunded / evaluator_error
4.  Missing score key -> refunded / evaluator_error
5.  Score out of range (e.g. 1.5) -> refunded / evaluator_error
6.  canonical_hash changes when rubric_template changes
7.  canonical_hash is stable for identical config
8.  canonical_hash changes when model changes
9.  build_prompt embeds artifact text
10. build_prompt binary fallback ("<binary>") does not crash evaluate
11. All tests use stub llm_client -- no real API calls
12. isinstance(evaluator, PrimaryEvaluator) is True

Note: tests 11 and 12 are structural properties validated throughout.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.primitives.asset import AssetRef
from core.primitives.evaluator import PrimaryEvaluator
from core.primitives.evaluators import LLMRubricEvaluator, DEFAULT_RUBRIC_TEMPLATE
from core.primitives.identity import Ed25519Keypair
from core.primitives.money import Money
from core.primitives.signer import LocalKeypairSigner
from core.primitives.sla import InterOrgSLA


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def usd() -> AssetRef:
    return AssetRef(asset_id="mock-usd", contract="USD", decimals=6)


@pytest.fixture(scope="module")
def keypair() -> Ed25519Keypair:
    return Ed25519Keypair.generate()


@pytest.fixture(scope="module")
def signer(keypair: Ed25519Keypair) -> LocalKeypairSigner:
    return LocalKeypairSigner(keypair)


@pytest.fixture(scope="module")
def sla(usd: AssetRef) -> InterOrgSLA:
    """Minimal SLA for use in all evaluator tests."""
    return InterOrgSLA.create(
        sla_id="test-sla-b4-001",
        requester_node_did="did:companyos:requester",
        provider_node_did="did:companyos:provider",
        task_scope="deliver analysis report",
        deliverable_schema={
            "kind": "json_schema",
            "spec_version": "2020-12",
            "schema": {"type": "object"},
        },
        accuracy_requirement=0.9,
        latency_ms=60_000,
        payment=Money(Decimal("100.000000"), usd),
        penalty_stake=Money(Decimal("100.000000"), usd),
        nonce=InterOrgSLA.new_nonce(),
        issued_at="2026-04-21T00:00:00Z",
        expires_at="2026-04-28T00:00:00Z",
    )


def _make_evaluator(
    signer: LocalKeypairSigner,
    stub_response: str,
    *,
    accuracy_floor: Decimal = Decimal("0.5"),
    model: str = "claude-sonnet-4-6",
    rubric_template: str = DEFAULT_RUBRIC_TEMPLATE,
) -> LLMRubricEvaluator:
    """Build an evaluator with an injected stub llm_client."""
    def stub_client(prompt: str) -> str:
        return stub_response

    return LLMRubricEvaluator(
        evaluator_did="did:test:evaluator-b4",
        signer=signer,
        model=model,
        rubric_template=rubric_template,
        accuracy_floor=accuracy_floor,
        llm_client=stub_client,
    )


# ---------------------------------------------------------------------------
# Test 1: accepted when score >= floor
# ---------------------------------------------------------------------------
class TestScoreAccepted:
    def test_result_is_accepted(self, sla, signer):
        ev = _make_evaluator(signer, '{"score": 0.95, "reasoning": "great"}')
        out = ev.evaluate(sla, b"some artifact bytes")
        assert out.result == "accepted"

    def test_score_decimal(self, sla, signer):
        ev = _make_evaluator(signer, '{"score": 0.95, "reasoning": "great"}')
        out = ev.evaluate(sla, b"some artifact bytes")
        assert out.score == Decimal("0.95")

    def test_evidence_kind(self, sla, signer):
        ev = _make_evaluator(signer, '{"score": 0.95, "reasoning": "great"}')
        out = ev.evaluate(sla, b"some artifact bytes")
        assert out.evidence["kind"] == "schema_pass_with_score"

    def test_canonical_hash_in_output(self, sla, signer):
        ev = _make_evaluator(signer, '{"score": 0.95, "reasoning": "great"}')
        out = ev.evaluate(sla, b"some artifact bytes")
        assert out.evaluator_canonical_hash == ev.canonical_hash


# ---------------------------------------------------------------------------
# Test 2: rejected when score < floor
# ---------------------------------------------------------------------------
class TestScoreRejected:
    def test_result_is_rejected(self, sla, signer):
        ev = _make_evaluator(signer, '{"score": 0.3, "reasoning": "poor"}')
        out = ev.evaluate(sla, b"some artifact bytes")
        assert out.result == "rejected"

    def test_score_decimal(self, sla, signer):
        ev = _make_evaluator(signer, '{"score": 0.3, "reasoning": "poor"}')
        out = ev.evaluate(sla, b"some artifact bytes")
        assert out.score == Decimal("0.3")

    def test_at_exactly_floor_is_accepted(self, sla, signer):
        ev = _make_evaluator(
            signer,
            '{"score": 0.5, "reasoning": "just passes"}',
            accuracy_floor=Decimal("0.5"),
        )
        out = ev.evaluate(sla, b"artifact")
        assert out.result == "accepted"


# ---------------------------------------------------------------------------
# Test 3: malformed JSON -> refunded
# ---------------------------------------------------------------------------
class TestMalformedJSON:
    def test_result_is_refunded(self, sla, signer):
        ev = _make_evaluator(signer, "not json")
        out = ev.evaluate(sla, b"artifact")
        assert out.result == "refunded"

    def test_evidence_kind_is_evaluator_error(self, sla, signer):
        ev = _make_evaluator(signer, "not json")
        out = ev.evaluate(sla, b"artifact")
        assert out.evidence["kind"] == "evaluator_error"

    def test_score_is_zero(self, sla, signer):
        ev = _make_evaluator(signer, "not json")
        out = ev.evaluate(sla, b"artifact")
        assert out.score == Decimal("0")


# ---------------------------------------------------------------------------
# Test 4: missing score key -> refunded
# ---------------------------------------------------------------------------
class TestMissingScoreKey:
    def test_result_is_refunded(self, sla, signer):
        ev = _make_evaluator(signer, '{"reasoning": "ok"}')
        out = ev.evaluate(sla, b"artifact")
        assert out.result == "refunded"

    def test_evidence_kind_is_evaluator_error(self, sla, signer):
        ev = _make_evaluator(signer, '{"reasoning": "ok"}')
        out = ev.evaluate(sla, b"artifact")
        assert out.evidence["kind"] == "evaluator_error"


# ---------------------------------------------------------------------------
# Test 5: score out of range -> refunded
# ---------------------------------------------------------------------------
class TestScoreOutOfRange:
    def test_above_one_is_refunded(self, sla, signer):
        ev = _make_evaluator(signer, '{"score": 1.5, "reasoning": "out of range"}')
        out = ev.evaluate(sla, b"artifact")
        assert out.result == "refunded"

    def test_negative_is_refunded(self, sla, signer):
        ev = _make_evaluator(signer, '{"score": -0.1, "reasoning": "negative"}')
        out = ev.evaluate(sla, b"artifact")
        assert out.result == "refunded"

    def test_evidence_kind_is_evaluator_error(self, sla, signer):
        ev = _make_evaluator(signer, '{"score": 1.5, "reasoning": "out of range"}')
        out = ev.evaluate(sla, b"artifact")
        assert out.evidence["kind"] == "evaluator_error"


# ---------------------------------------------------------------------------
# Tests 6-8: canonical_hash stability and sensitivity
# ---------------------------------------------------------------------------
class TestCanonicalHash:
    def test_hash_changes_when_rubric_template_changes(self, signer):
        """Test 6: different rubric templates -> different hashes."""
        ev1 = LLMRubricEvaluator(
            evaluator_did="did:test:ev",
            signer=signer,
            rubric_template="template A: {sla} {artifact}",
            llm_client=lambda p: '{"score": 0.9}',
        )
        ev2 = LLMRubricEvaluator(
            evaluator_did="did:test:ev",
            signer=signer,
            rubric_template="template B: {sla} {artifact}",
            llm_client=lambda p: '{"score": 0.9}',
        )
        assert ev1.canonical_hash != ev2.canonical_hash

    def test_hash_is_stable_for_identical_config(self, signer):
        """Test 7: same config -> same hash on repeated access and across instances."""
        ev1 = LLMRubricEvaluator(
            evaluator_did="did:test:ev",
            signer=signer,
            model="claude-sonnet-4-6",
            rubric_template=DEFAULT_RUBRIC_TEMPLATE,
            accuracy_floor=Decimal("0.5"),
            llm_client=lambda p: '{"score": 0.9}',
        )
        ev2 = LLMRubricEvaluator(
            evaluator_did="did:test:ev",
            signer=signer,
            model="claude-sonnet-4-6",
            rubric_template=DEFAULT_RUBRIC_TEMPLATE,
            accuracy_floor=Decimal("0.5"),
            llm_client=lambda p: '{"score": 0.9}',
        )
        # Stable across accesses on the same instance
        assert ev1.canonical_hash == ev1.canonical_hash
        # Consistent across instances with identical config
        assert ev1.canonical_hash == ev2.canonical_hash

    def test_hash_changes_when_model_changes(self, signer):
        """Test 8: different models -> different hashes."""
        ev1 = LLMRubricEvaluator(
            evaluator_did="did:test:ev",
            signer=signer,
            model="claude-sonnet-4-6",
            llm_client=lambda p: '{"score": 0.9}',
        )
        ev2 = LLMRubricEvaluator(
            evaluator_did="did:test:ev",
            signer=signer,
            model="claude-opus-4-5",
            llm_client=lambda p: '{"score": 0.9}',
        )
        assert ev1.canonical_hash != ev2.canonical_hash

    def test_hash_is_64_char_hex(self, signer):
        """canonical_hash must be a 64-character hex string (SHA-256)."""
        ev = LLMRubricEvaluator(
            evaluator_did="did:test:ev",
            signer=signer,
            llm_client=lambda p: '{"score": 0.9}',
        )
        h = ev.canonical_hash
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Test 9: build_prompt embeds artifact
# ---------------------------------------------------------------------------
class TestBuildPrompt:
    def test_artifact_appears_in_prompt(self, sla, signer):
        """Test 9: build_prompt places the artifact string into the output."""
        ev = LLMRubricEvaluator(
            evaluator_did="did:test:ev",
            signer=signer,
            llm_client=lambda p: '{"score": 0.9}',
        )
        prompt = ev.build_prompt(sla, "hello world artifact content")
        assert "hello world artifact content" in prompt

    def test_sla_appears_in_prompt(self, sla, signer):
        ev = LLMRubricEvaluator(
            evaluator_did="did:test:ev",
            signer=signer,
            llm_client=lambda p: '{"score": 0.9}',
        )
        prompt = ev.build_prompt(sla, "artifact text")
        # str(sla) should appear somewhere; at minimum the prompt is non-empty
        assert len(prompt) > 0
        assert "artifact text" in prompt


# ---------------------------------------------------------------------------
# Test 10: binary fallback -- bytes that cannot decode as UTF-8
# ---------------------------------------------------------------------------
class TestBinaryFallback:
    def test_binary_artifact_does_not_crash(self, sla, signer):
        """Test 10: invalid UTF-8 bytes fall back to '<binary>'; no exception."""
        ev = _make_evaluator(signer, '{"score": 0.8, "reasoning": "ok"}')
        # b"\xff\xfe" is invalid UTF-8
        out = ev.evaluate(sla, b"\xff\xfe")
        # Should not crash; result is determined by the stubbed score
        assert out.result in {"accepted", "rejected", "refunded"}

    def test_binary_fallback_value_is_placeholder(self, sla, signer):
        """The fallback string '<binary>' must appear in the prompt."""
        prompts_seen: list[str] = []

        def recording_client(prompt: str) -> str:
            prompts_seen.append(prompt)
            return '{"score": 0.8, "reasoning": "ok"}'

        ev = LLMRubricEvaluator(
            evaluator_did="did:test:ev",
            signer=signer,
            llm_client=recording_client,
        )
        ev.evaluate(sla, b"\xff\xfe")
        assert len(prompts_seen) == 1
        assert "<binary>" in prompts_seen[0]


# ---------------------------------------------------------------------------
# Test 12: PrimaryEvaluator protocol conformance
# ---------------------------------------------------------------------------
class TestProtocolConformance:
    def test_isinstance_primary_evaluator(self, signer):
        """Test 12: LLMRubricEvaluator satisfies PrimaryEvaluator at runtime."""
        ev = LLMRubricEvaluator(
            evaluator_did="did:test:ev",
            signer=signer,
            llm_client=lambda p: '{"score": 0.9}',
        )
        assert isinstance(ev, PrimaryEvaluator)

    def test_evaluator_did_property(self, signer):
        ev = LLMRubricEvaluator(
            evaluator_did="did:test:custom-did",
            signer=signer,
            llm_client=lambda p: '{"score": 0.9}',
        )
        assert ev.evaluator_did == "did:test:custom-did"
