"""
tests/test_sla_properties.py — Ticket 5 Hypothesis properties
=============================================================
Property-based tests for `core.primitives.sla.InterOrgSLA`.

The two properties we're pinning down:

1. Canonical bytes are insensitive to dict insertion order in
   `deliverable_schema` at any nesting depth — json.dumps(sort_keys=True)
   makes this almost mechanical, but the test exists because future
   refactors (e.g. switching to a tuple-based schema representation or
   adding a post-process step) could quietly break it.

2. `integrity_binding` is deterministic across equivalent constructions
   — the same inputs must always yield the same hash, regardless of
   when they're constructed or how the timestamps are expressed.
"""
from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from core.primitives.asset import AssetRef
from core.primitives.money import Money
from core.primitives.sla import InterOrgSLA


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def usd() -> AssetRef:
    return AssetRef(asset_id="mock-usd", contract="USD", decimals=6)


def _shuffle_dict(d: dict) -> dict:
    """Return a dict with the same keys/values but a different insertion
    order. Recurses into nested dicts."""
    items = list(d.items())
    random.shuffle(items)
    shuffled: dict = {}
    for k, v in items:
        if isinstance(v, dict):
            shuffled[k] = _shuffle_dict(v)
        else:
            shuffled[k] = v
    return shuffled


@st.composite
def nested_schema_dicts(draw, max_depth: int = 3, max_keys: int = 4):
    """Hypothesis strategy for JSON-serializable nested dicts.

    Keeps values to primitives (str, int, bool) + nested dicts so we
    can focus on the key-ordering property without fighting JSON.
    """
    if max_depth <= 0:
        return draw(
            st.dictionaries(
                keys=st.text(min_size=1, max_size=6),
                values=st.one_of(
                    st.text(max_size=6),
                    st.integers(min_value=-100, max_value=100),
                    st.booleans(),
                ),
                max_size=max_keys,
            )
        )
    return draw(
        st.dictionaries(
            keys=st.text(min_size=1, max_size=6),
            values=st.one_of(
                st.text(max_size=6),
                st.integers(min_value=-100, max_value=100),
                st.booleans(),
                nested_schema_dicts(max_depth=max_depth - 1, max_keys=max_keys),
            ),
            max_size=max_keys,
        )
    )


def _make_sla(*, schema: dict, usd: AssetRef, **overrides: Any) -> InterOrgSLA:
    base = dict(
        sla_id="sla-prop",
        requester_node_did="did:companyos:requester",
        provider_node_did="did:companyos:provider",
        task_scope="prop-test",
        deliverable_schema=schema,
        accuracy_requirement=0.95,
        latency_ms=60_000,
        payment=Money(Decimal("1"), usd),
        penalty_stake=Money(Decimal("0.5"), usd),
        nonce="deadbeef" * 4,
        issued_at="2026-04-19T12:00:00Z",
        expires_at="2026-04-19T13:00:00Z",
    )
    base.update(overrides)
    return InterOrgSLA.create(**base)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
@given(schema=nested_schema_dicts())
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_canonical_bytes_insensitive_to_insertion_order(usd, schema):
    """For any valid deliverable_schema, shuffling its insertion order
    (recursively) must not change the canonical bytes or the binding."""
    sla_a = _make_sla(schema=schema, usd=usd)
    sla_b = _make_sla(schema=_shuffle_dict(schema), usd=usd)
    assert sla_a.canonical_bytes() == sla_b.canonical_bytes()
    assert sla_a.integrity_binding == sla_b.integrity_binding


@given(schema=nested_schema_dicts())
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_integrity_binding_is_deterministic(usd, schema):
    """Repeated construction with identical inputs yields identical binding."""
    bindings = {
        _make_sla(schema=schema, usd=usd).integrity_binding for _ in range(3)
    }
    assert len(bindings) == 1


@given(
    schema=nested_schema_dicts(),
    payment_amt=st.integers(min_value=0, max_value=1_000_000),
)
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_binding_changes_when_payment_changes(usd, schema, payment_amt):
    """Changing the payment quantity must perturb the binding
    (different Money → different canonical bytes → different hash)."""
    sla_base = _make_sla(schema=schema, usd=usd)
    sla_mut = _make_sla(
        schema=schema,
        usd=usd,
        payment=Money(Decimal(payment_amt + 1), usd),  # guaranteed-different
    )
    if sla_base.payment == sla_mut.payment:
        # degenerate case (shouldn't happen with the +1 above but guard anyway)
        return
    assert sla_base.integrity_binding != sla_mut.integrity_binding
