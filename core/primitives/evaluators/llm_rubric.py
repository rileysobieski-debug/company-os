"""
core/primitives/evaluators/llm_rubric.py -- LLMRubricEvaluator (B4)
====================================================================
Ticket B4 of the v1b Oracle build. Provides `LLMRubricEvaluator`, the
reference Tier 1 evaluator that uses an LLM to score an artifact against
an SLA rubric and returns a structured `EvaluationOutput`.

Design decisions
----------------
- `llm_client` is an optional injectable callable `(prompt: str) -> str`.
  When None, the evaluator wraps `core.llm_client.single_turn` to produce
  the same signature so call sites are uniform.
- Score parsing is delegated to `score_parser.extract_score`, which accepts
  real-world LLM response shapes (bare floats, labeled scores, percents,
  JSON-embedded values, prose-wrapped numbers). Any parse failure
  (malformed input, missing score, out-of-range) returns a `"refunded"`
  output with `evidence.kind = "evaluator_error"` rather than raising --
  evaluators must not propagate exceptions to the Oracle.
- `canonical_hash` is computed at access time (not cached at construction)
  so it is always consistent with the current field values. It is cheap
  enough that repeated access is not a concern.
- Binary artifact bytes that cannot decode as UTF-8 fall back to the
  literal string `"<binary>"` so the LLM receives a meaningful signal
  rather than crashing.

Evidence kinds used
-------------------
- `"schema_pass_with_score"` -- successful evaluation; score attached.
- `"evaluator_error"` -- any failure path (LLM error, parse error, etc.).

Both kinds are in `_VALID_EVIDENCE_KINDS` (oracle.py) so `EvaluationOutput`
construction never raises a ValueError on the kind field.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from core.primitives.evaluator import EvaluationOutput, PrimaryEvaluator
from core.primitives.evaluators.score_parser import extract_score
from core.primitives.signer import Signer
from core.primitives.sla import InterOrgSLA


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
VERSION = "0.1"

DEFAULT_RUBRIC_TEMPLATE = """\
Evaluate the following artifact against the SLA requirements.

SLA: {sla}
Artifact: {artifact}

Respond with a JSON object: {{"score": <float 0.0-1.0>, "reasoning": "<string>"}}.
Score 1.0 means fully meets requirements; 0.0 means completely fails.\
"""


# ---------------------------------------------------------------------------
# LLMRubricEvaluator
# ---------------------------------------------------------------------------
@dataclass
class LLMRubricEvaluator:
    """Reference Tier 1 evaluator: scores artifacts via an LLM rubric.

    Parameters
    ----------
    evaluator_did:
        The DID of this evaluator node, stamped into every EvaluationOutput.
    signer:
        A `Signer` instance (not used directly in scoring, but required by
        the protocol so downstream code can verify the evaluator's identity).
    model:
        Anthropic model ID to use when `llm_client` is None and the default
        `single_turn` wrapper is used.
    rubric_template:
        Format string with `{sla}` and `{artifact}` placeholders.  Defaults
        to `DEFAULT_RUBRIC_TEMPLATE`.
    accuracy_floor:
        Minimum score required for `result="accepted"`.  Default 0.5.
    llm_client:
        Optional callable `(prompt: str) -> str`.  When None, the evaluator
        resolves `core.llm_client.single_turn` at call time (lazy import so
        the module is usable in test environments without `anthropic`).
    """

    evaluator_did: str
    signer: Signer
    model: str = "claude-sonnet-4-6"
    rubric_template: str = DEFAULT_RUBRIC_TEMPLATE
    accuracy_floor: Decimal = Decimal("0.5")
    llm_client: Any = None

    # ------------------------------------------------------------------
    # PrimaryEvaluator protocol members
    # ------------------------------------------------------------------
    @property
    def canonical_hash(self) -> str:
        """Stable 64-char SHA-256 hex digest pinning this evaluator's algorithm.

        Incorporates class name, version, model, rubric template hash, and
        accuracy floor so that any change to the evaluation algorithm produces
        a different hash.
        """
        class_name = "LLMRubricEvaluator"
        version = VERSION
        rubric_template_hash = hashlib.sha256(
            self.rubric_template.encode()
        ).hexdigest()
        accuracy_floor_str = str(self.accuracy_floor)

        canonical_str = (
            f"{class_name}:{version}:{self.model}:"
            f"{rubric_template_hash}:{accuracy_floor_str}"
        )
        return hashlib.sha256(canonical_str.encode()).hexdigest()

    def evaluate(
        self,
        sla: InterOrgSLA,
        artifact_bytes: bytes,
        *,
        artifact_properties: dict | None = None,
    ) -> EvaluationOutput:
        """Evaluate an artifact against an SLA using the configured rubric.

        Steps:
          1. Decode the artifact bytes (UTF-8; falls back to "<binary>").
          2. Render the rubric prompt.
          3. Call the LLM client.
          4. Extract a validated score from the response via
             `score_parser.extract_score`. Returns Decimal in [0, 1] or None.
          5. Return an EvaluationOutput with result "accepted" or "rejected".

        On any failure (LLM error, unparseable response, out-of-range score),
        returns result="refunded" with evidence.kind="evaluator_error".
        """
        try:
            artifact_str = self._decode_artifact(artifact_bytes)
            prompt = self.build_prompt(sla, artifact_str)
            response_text = self._call_llm(prompt)
            score = extract_score(response_text)
            if score is None:
                raise ValueError(
                    "LLM response did not contain a parseable score in [0, 1]"
                )
            result = "accepted" if score >= self.accuracy_floor else "rejected"
            return EvaluationOutput(
                result=result,
                score=score,
                evidence={
                    "kind": "schema_pass_with_score",
                    "raw_response": response_text,
                },
                evaluator_canonical_hash=self.canonical_hash,
            )
        except Exception as exc:  # noqa: BLE001 -- evaluators must never propagate
            return EvaluationOutput(
                result="refunded",
                score=Decimal("0"),
                evidence={
                    "kind": "evaluator_error",
                    "detail": str(exc),
                },
                evaluator_canonical_hash=self.canonical_hash,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def build_prompt(self, sla: InterOrgSLA, artifact_str: str) -> str:
        """Render the rubric template with the given SLA and artifact string."""
        return self.rubric_template.format(sla=sla, artifact=artifact_str)

    def _decode_artifact(self, artifact_bytes: bytes) -> str:
        """Decode bytes as UTF-8; fall back to '<binary>' on decode error."""
        try:
            return artifact_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return "<binary>"

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM and return the text response.

        Uses the injected `llm_client` when set; otherwise falls back to
        a thin wrapper around `core.llm_client.single_turn`.

        Raises any exception from the client -- the caller (`evaluate`)
        catches and converts to a refunded output.
        """
        if self.llm_client is not None:
            return self.llm_client(prompt)

        # Lazy import so this module is importable without `anthropic`.
        from core.llm_client import single_turn  # noqa: PLC0415

        response = single_turn(
            messages=[{"role": "user", "content": prompt}],
            model=self.model,
            cost_tag="llm_rubric_evaluator",
        )
        if response.error:
            raise RuntimeError(response.error)
        return response.text


__all__ = [
    "VERSION",
    "DEFAULT_RUBRIC_TEMPLATE",
    "LLMRubricEvaluator",
]
