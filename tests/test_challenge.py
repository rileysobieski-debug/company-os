"""
tests/test_challenge.py -- Ticket B1-c coverage for Challenge primitive
=======================================================================
Unit tests for `core.primitives.challenge.Challenge`:

- Sign + verify round trip
- to_dict -> from_dict -> verify_signature preserves validity
- Tamper on reason or challenger_did raises SignatureError
- prior_verdict_hash in Challenge equals prior_verdict.verdict_hash
- Empty reason raises ValueError
- Reason longer than 2000 chars raises ValueError
- Empty challenger_did raises ValueError
- Non-counterparty challenger_did constructs OK (no authorization at
  primitive level -- authorization is an adapter-boundary concern)
- Challenge.create requires a Signer (not a raw keypair)
- Canonical determinism: two Challenges from identical inputs share
  challenge_hash
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from core.primitives.challenge import Challenge
from core.primitives.exceptions import SignatureError
from core.primitives.identity import Ed25519Keypair
from core.primitives.oracle import OracleVerdict
from core.primitives.signer import LocalKeypairSigner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def keypair() -> Ed25519Keypair:
    return Ed25519Keypair.generate()


@pytest.fixture
def other_keypair() -> Ed25519Keypair:
    return Ed25519Keypair.generate()


@pytest.fixture
def signer(keypair) -> LocalKeypairSigner:
    return LocalKeypairSigner(keypair)


@pytest.fixture
def verdict(keypair) -> OracleVerdict:
    """A valid signed OracleVerdict used as the prior_verdict in challenge tests."""
    return OracleVerdict.create(
        sla_id="sla-test-001",
        artifact_hash="a" * 64,
        tier=1,
        result="rejected",
        evaluator_did="did:companyos:evaluator-001",
        evidence={"kind": "schema_fail", "detail": "field missing"},
        issued_at="2026-04-21T09:00:00Z",
        signer=LocalKeypairSigner(keypair),
    )


def _base_kwargs(verdict: OracleVerdict, signer: LocalKeypairSigner) -> dict:
    """Valid kwargs bundle for `Challenge.create`."""
    return {
        "prior_verdict": verdict,
        "challenger_did": "did:companyos:provider-001",
        "reason": "The artifact meets spec; evaluator scoring is incorrect.",
        "signer": signer,
    }


# ---------------------------------------------------------------------------
# Sign + verify round trip
# ---------------------------------------------------------------------------
class TestSignVerifyRoundTrip:
    def test_create_and_verify(self, verdict, signer):
        """Fresh Challenge verifies without error."""
        c = Challenge.create(**_base_kwargs(verdict, signer))
        c.verify_signature()  # must not raise

    def test_to_dict_from_dict_then_verify(self, verdict, signer):
        """create -> to_dict -> from_dict -> verify_signature must pass."""
        c = Challenge.create(**_base_kwargs(verdict, signer))
        rehydrated = Challenge.from_dict(c.to_dict())
        rehydrated.verify_signature()  # must not raise

    def test_round_trip_preserves_all_fields(self, verdict, signer):
        """from_dict(to_dict()) produces an equal frozen instance."""
        c = Challenge.create(**_base_kwargs(verdict, signer))
        rehydrated = Challenge.from_dict(c.to_dict())
        assert rehydrated == c

    def test_prior_verdict_hash_matches_verdict(self, verdict, signer):
        """challenge.prior_verdict_hash == prior_verdict.verdict_hash."""
        c = Challenge.create(**_base_kwargs(verdict, signer))
        assert c.prior_verdict_hash == verdict.verdict_hash

    def test_protocol_version_default(self, verdict, signer):
        c = Challenge.create(**_base_kwargs(verdict, signer))
        assert c.protocol_version == "companyos-challenge/0.1"

    def test_signer_field_matches_keypair_public_key(self, verdict, signer, keypair):
        c = Challenge.create(**_base_kwargs(verdict, signer))
        assert c.signer == keypair.public_key
        assert c.signature.signer == keypair.public_key

    def test_challenge_hash_is_non_empty_hex(self, verdict, signer):
        c = Challenge.create(**_base_kwargs(verdict, signer))
        assert isinstance(c.challenge_hash, str)
        assert len(c.challenge_hash) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------
class TestTamperDetection:
    def test_tamper_reason_raises_signature_error(self, verdict, signer):
        """Mutating reason via dataclasses.replace invalidates the signature."""
        c = Challenge.create(**_base_kwargs(verdict, signer))
        tampered = dataclasses.replace(c, reason="Injected reason.")
        with pytest.raises(SignatureError):
            tampered.verify_signature()

    def test_tamper_challenger_did_raises_signature_error(self, verdict, signer):
        """Mutating challenger_did invalidates the signature."""
        c = Challenge.create(**_base_kwargs(verdict, signer))
        tampered = dataclasses.replace(
            c, challenger_did="did:companyos:attacker-999"
        )
        with pytest.raises(SignatureError):
            tampered.verify_signature()

    def test_tamper_prior_verdict_hash_raises_signature_error(self, verdict, signer):
        """Mutating prior_verdict_hash invalidates the signature."""
        c = Challenge.create(**_base_kwargs(verdict, signer))
        tampered = dataclasses.replace(c, prior_verdict_hash="b" * 64)
        with pytest.raises(SignatureError):
            tampered.verify_signature()

    def test_signer_mismatch_raises_signature_error_before_crypto(
        self, verdict, signer, other_keypair
    ):
        """Swapping `signer` without updating `signature` raises SignatureError
        on the consistency check before crypto verification runs."""
        c = Challenge.create(**_base_kwargs(verdict, signer))
        tampered = dataclasses.replace(c, signer=other_keypair.public_key)
        with pytest.raises(SignatureError, match="signer"):
            tampered.verify_signature()


# ---------------------------------------------------------------------------
# Input validation at create
# ---------------------------------------------------------------------------
class TestInputValidation:
    def test_empty_reason_raises_value_error(self, verdict, signer):
        with pytest.raises(ValueError, match="reason"):
            Challenge.create(
                prior_verdict=verdict,
                challenger_did="did:companyos:provider-001",
                reason="",
                signer=signer,
            )

    def test_reason_over_2000_chars_raises_value_error(self, verdict, signer):
        long_reason = "x" * 2001
        with pytest.raises(ValueError, match="reason"):
            Challenge.create(
                prior_verdict=verdict,
                challenger_did="did:companyos:provider-001",
                reason=long_reason,
                signer=signer,
            )

    def test_reason_exactly_2000_chars_is_accepted(self, verdict, signer):
        """Boundary: exactly 2000 chars must succeed."""
        boundary_reason = "y" * 2000
        c = Challenge.create(
            prior_verdict=verdict,
            challenger_did="did:companyos:provider-001",
            reason=boundary_reason,
            signer=signer,
        )
        assert len(c.reason) == 2000

    def test_empty_challenger_did_raises_value_error(self, verdict, signer):
        with pytest.raises(ValueError, match="challenger_did"):
            Challenge.create(
                prior_verdict=verdict,
                challenger_did="",
                reason="Valid reason.",
                signer=signer,
            )

    def test_wrong_prior_verdict_type_raises_type_error(self, signer):
        """Passing a non-OracleVerdict raises TypeError."""
        with pytest.raises(TypeError, match="prior_verdict"):
            Challenge.create(
                prior_verdict={"verdict_hash": "abc"},  # type: ignore[arg-type]
                challenger_did="did:companyos:provider-001",
                reason="Valid reason.",
                signer=signer,
            )

    def test_raw_keypair_as_signer_raises_type_error(self, verdict, keypair):
        """Passing a raw Ed25519Keypair (not a Signer) raises TypeError."""
        with pytest.raises(TypeError, match="signer"):
            Challenge.create(
                prior_verdict=verdict,
                challenger_did="did:companyos:provider-001",
                reason="Valid reason.",
                signer=keypair,  # type: ignore[arg-type]
            )

    def test_non_counterparty_challenger_did_constructs_ok(self, verdict, signer):
        """Authorization is NOT enforced at the primitive level.

        A challenger_did that has no relation to the underlying SLA
        must still produce a valid Challenge. Authorization is checked at
        the adapter boundary (B3).
        """
        c = Challenge.create(
            prior_verdict=verdict,
            challenger_did="did:companyos:completely-unrelated-party",
            reason="Filing a challenge regardless of authorization.",
            signer=signer,
        )
        assert c.challenger_did == "did:companyos:completely-unrelated-party"
        c.verify_signature()  # must not raise


# ---------------------------------------------------------------------------
# Canonical determinism
# ---------------------------------------------------------------------------
class TestCanonicalDeterminism:
    def test_same_inputs_yield_same_challenge_hash(self, verdict, signer):
        """Two Challenges built from identical inputs share challenge_hash.

        Ed25519 is deterministic, so the signature is also identical.
        """
        kwargs = _base_kwargs(verdict, signer)
        # We cannot control `issued_at` from outside, so we check that the
        # challenge_hash stays stable across repeated calls WITHIN the same
        # second (same issued_at). We verify by constructing twice and
        # checking hash equality when issued_at matches.
        c1 = Challenge.create(**kwargs)
        c2 = Challenge.create(**kwargs)
        # If issued_at differs by a clock tick, hashes may differ legitimately;
        # we only assert equality when the timestamps match.
        if c1.issued_at == c2.issued_at:
            assert c1.challenge_hash == c2.challenge_hash

    def test_signature_excluded_from_canonical_bytes(self, verdict, signer):
        """Canonical bytes of a Challenge must not contain the `signature` key."""
        from core.primitives.challenge import _challenge_canonical_bytes

        c = Challenge.create(**_base_kwargs(verdict, signer))
        canon = _challenge_canonical_bytes(c, False)
        parsed = json.loads(canon.decode("utf-8"))
        assert "signature" not in parsed

    def test_challenge_hash_excluded_when_flag_set(self, verdict, signer):
        """challenge_hash is absent from canonical bytes when flag is True."""
        from core.primitives.challenge import _challenge_canonical_bytes

        c = Challenge.create(**_base_kwargs(verdict, signer))
        body_no_hash = _challenge_canonical_bytes(c, True)
        parsed = json.loads(body_no_hash.decode("utf-8"))
        assert "challenge_hash" not in parsed
        assert "signature" not in parsed

    def test_challenge_hash_included_in_signing_body(self, verdict, signer):
        """Signing body includes challenge_hash (signature commits to hash)."""
        from core.primitives.challenge import _challenge_canonical_bytes

        c = Challenge.create(**_base_kwargs(verdict, signer))
        signing_body = _challenge_canonical_bytes(c, False)
        parsed = json.loads(signing_body.decode("utf-8"))
        assert "challenge_hash" in parsed
        assert "signature" not in parsed

    def test_different_reason_changes_challenge_hash(self, verdict, signer):
        kwargs1 = _base_kwargs(verdict, signer)
        kwargs2 = dict(kwargs1, reason="A completely different reason.")
        c1 = Challenge.create(**kwargs1)
        c2 = Challenge.create(**kwargs2)
        assert c1.challenge_hash != c2.challenge_hash

    def test_different_challenger_did_changes_challenge_hash(self, verdict, signer):
        kwargs1 = _base_kwargs(verdict, signer)
        kwargs2 = dict(kwargs1, challenger_did="did:companyos:other-party")
        c1 = Challenge.create(**kwargs1)
        c2 = Challenge.create(**kwargs2)
        assert c1.challenge_hash != c2.challenge_hash


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
class TestSerialization:
    def test_to_dict_is_json_serializable(self, verdict, signer):
        """to_dict output serializes to JSON without errors."""
        c = Challenge.create(**_base_kwargs(verdict, signer))
        encoded = json.dumps(c.to_dict(), sort_keys=True, separators=(",", ":"))
        decoded = json.loads(encoded)
        assert decoded["protocol_version"] == "companyos-challenge/0.1"
        assert decoded["challenger_did"] == "did:companyos:provider-001"

    def test_signer_round_trips_as_bytes_hex_dict(self, verdict, signer, keypair):
        """to_dict serializes signer as {"bytes_hex": "..."}, not a string."""
        c = Challenge.create(**_base_kwargs(verdict, signer))
        d = c.to_dict()
        assert isinstance(d["signer"], dict)
        assert "bytes_hex" in d["signer"]
        assert d["signer"]["bytes_hex"] == keypair.public_key.bytes_hex

    def test_from_dict_missing_field_raises_value_error(self, verdict, signer):
        """from_dict raises ValueError for each missing required field."""
        c = Challenge.create(**_base_kwargs(verdict, signer))
        required_fields = (
            "prior_verdict_hash",
            "challenger_did",
            "reason",
            "challenge_hash",
            "signer",
            "signature",
            "issued_at",
        )
        for field in required_fields:
            d = c.to_dict()
            del d[field]
            with pytest.raises(ValueError, match=field):
                Challenge.from_dict(d)

    def test_from_dict_unknown_protocol_version_constructs_ok(self, verdict, signer):
        """from_dict with an unknown protocol_version succeeds at load time.

        verify_signature will raise ValueError later when registry dispatch
        fails. This matches OracleVerdict.from_dict behavior.
        """
        c = Challenge.create(**_base_kwargs(verdict, signer))
        d = c.to_dict()
        d["protocol_version"] = "companyos-challenge/99.99"
        rehydrated = Challenge.from_dict(d)
        assert rehydrated.protocol_version == "companyos-challenge/99.99"
        # verify_signature raises ValueError (unknown version), not SignatureError
        with pytest.raises(ValueError):
            rehydrated.verify_signature()

    def test_protocol_version_default_round_trips(self, verdict, signer):
        c = Challenge.create(**_base_kwargs(verdict, signer))
        d = c.to_dict()
        del d["protocol_version"]
        rehydrated = Challenge.from_dict(d)
        assert rehydrated.protocol_version == "companyos-challenge/0.1"
