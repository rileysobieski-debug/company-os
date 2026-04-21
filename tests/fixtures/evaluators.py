"""
tests/fixtures/evaluators.py -- Evaluator test doubles
=======================================================
Shared fixtures for B1, B2, and B5 oracle tests.

`StubPassthroughEvaluator` satisfies the `PrimaryEvaluator` protocol without
performing any real evaluation. It returns a canned `EvaluationOutput` on
every `evaluate()` call, making test assertions deterministic regardless of
the SLA or artifact passed in.

Why a passthrough rather than a mock
--------------------------------------
`unittest.mock.MagicMock` would satisfy isinstance(x, PrimaryEvaluator)
only if explicitly configured to expose the right properties. A concrete
stub class that directly implements the protocol is more readable in test
failures and avoids mock-configuration boilerplate in each test.
"""
from __future__ import annotations

from core.primitives.evaluator import EvaluationOutput, PrimaryEvaluator
from core.primitives.sla import InterOrgSLA


class StubPassthroughEvaluator:
    """Test double that satisfies PrimaryEvaluator and returns a canned output.

    Construct with the DID, canonical_hash, and the EvaluationOutput to
    return. Every call to `evaluate()` ignores its arguments and returns
    the canned output verbatim.

    Used by tests in B2 and B5 where the evaluator's internal logic is
    irrelevant to what the test is asserting.
    """

    def __init__(
        self,
        evaluator_did: str,
        canonical_hash: str,
        canned_output: EvaluationOutput,
    ) -> None:
        self._evaluator_did = evaluator_did
        self._canonical_hash = canonical_hash
        self._canned_output = canned_output

    @property
    def evaluator_did(self) -> str:
        return self._evaluator_did

    @property
    def canonical_hash(self) -> str:
        return self._canonical_hash

    def evaluate(
        self,
        sla: InterOrgSLA,
        artifact_bytes: bytes,
        *,
        artifact_properties: dict | None = None,
    ) -> EvaluationOutput:
        """Return the canned output regardless of inputs."""
        return self._canned_output


__all__ = ["StubPassthroughEvaluator"]
