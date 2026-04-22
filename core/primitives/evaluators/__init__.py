"""
core/primitives/evaluators/__init__.py -- Evaluator implementations (B4)
=========================================================================
Exports the concrete evaluator classes introduced in the v1b Oracle build.

`LLMRubricEvaluator`
    Reference Tier 1 evaluator: scores artifacts via an LLM rubric and
    returns a structured `EvaluationOutput`.  See `llm_rubric.py` for the
    full implementation and design notes.
"""

from core.primitives.evaluators.llm_rubric import (
    DEFAULT_RUBRIC_TEMPLATE,
    VERSION,
    LLMRubricEvaluator,
)

__all__ = [
    "VERSION",
    "DEFAULT_RUBRIC_TEMPLATE",
    "LLMRubricEvaluator",
]
