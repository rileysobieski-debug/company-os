# Asset Registry — How to Add a New Asset + Adapter

This directory holds one YAML file per settlement asset that Company OS
knows how to price, escrow, and transfer. The YAMLs are loaded at
startup by `core.primitives.asset.AssetRegistry`. A settlement
`AdapterRegistry` then pairs each `AssetRef` with exactly one
`SettlementAdapter` that knows how to move the asset.

This document covers: (1) the YAML schema, (2) how `AssetRegistry`
loads those files, (3) the `SettlementAdapter` Protocol and how the
`AdapterRegistry` enforces one-adapter-per-asset, and (4) a 20-line
walkthrough for adding a third asset.

For canonical-serialization / byte-hashing rules (which are what
actually make SLAs in different currencies hash-compatible), see the
module docstring of [`core/primitives/sla.py`](../sla.py).

## 1. YAML schema

Each file under `core/primitives/asset_registry/*.yaml` describes one
asset. All four keys are accepted; `asset_id` is required, the rest
have defaults:

```yaml
# mock-usd.yaml
asset_id: mock-usd       # (required) registry key — must be unique
chain_id: ""             # chain identifier (e.g. "base-mainnet"); "" for mocks
contract: "USD"          # contract address for on-chain, ticker/symbol for mocks
decimals: 6              # Money precision — 6 for USDC, 2 for EUR cash, etc.
```

Notes:
- `asset_id` is the *primary key* of the registry. Two files with the
  same `asset_id` silently overwrite (last-load-wins), so keep IDs
  unique.
- `decimals` drives `Money` quantization. `Money(Decimal("1.0001"), usd)`
  raises `InexactQuantizationError` if `usd.decimals < 4`. Pick the
  precision of the underlying rail — six for most on-chain stablecoins,
  two for card-rail fiat.
- `chain_id` and `contract` are informational in v0; `v1` adapters use
  them to pick the right RPC + token contract.

## 2. Loading: `AssetRegistry`

`AssetRegistry` is **deliberately not a singleton**. Every caller —
tests, adapters, the simulator — builds its own and calls `.load(root)`
with an explicit directory:

```python
from pathlib import Path
from core.primitives import AssetRegistry

reg = AssetRegistry()
reg.load(Path("core/primitives/asset_registry"))
usd = reg.get("mock-usd")  # -> AssetRef(asset_id='mock-usd', ..., decimals=6)
print(reg.ids())           # -> ['mock-eur', 'mock-usd', 'usdc-base']
```

No module-level instance exists, which keeps tests isolated and
prevents subtle cross-run state leaks. Each node process owns its own
registry.

## 3. Adapter Protocol + `AdapterRegistry`

A `SettlementAdapter` is a structural (`@runtime_checkable`) Protocol
defined in `core/primitives/settlement_adapters/base.py`:

```python
class SettlementAdapter(Protocol):
    def supports(self, asset: AssetRef) -> bool: ...
    def lock(self, amount: Money, ref: str, *, nonce: str) -> EscrowHandle: ...
    def release(self, handle: EscrowHandle, to: str) -> SettlementReceipt: ...
    def slash(self, handle: EscrowHandle, percent: int,
              beneficiary: str | None) -> SettlementReceipt: ...
    def balance(self, principal: str, asset: AssetRef) -> Money: ...
    def get_status(self, handle: EscrowHandle) -> EscrowStatus: ...
```

`supports(asset)` is the *dynamic capability declaration*. One adapter
can cover a family of assets (e.g. a future EVM adapter handling USDC,
DAI, and ETH on Base) — the registry doesn't care how `supports` is
implemented, only that it returns a bool.

`AdapterRegistry.register(adapter)` takes one adapter at a time and
raises `AdapterConflictError` the moment an incoming adapter's
`supports()` overlaps with a previously-registered adapter. There is
no first-match-wins fallback:

```python
from core.primitives import (
    AssetRegistry, AdapterRegistry,
    MockSettlementAdapter, AdapterConflictError,
)

asset_reg = AssetRegistry()
asset_reg.load(Path("core/primitives/asset_registry"))
usd = asset_reg.get("mock-usd")

adapters = AdapterRegistry(asset_reg)
adapters.register(MockSettlementAdapter(supported_assets=(usd,)))

# Registering a second adapter that also claims mock-usd blows up.
try:
    adapters.register(MockSettlementAdapter(supported_assets=(usd,)))
except AdapterConflictError as exc:
    print("conflict detected:", exc)
```

## 4. Add a third asset in 20 lines

Say you want to settle in **mock-gbp** (2 decimals). Steps:

### Step 1 — Create the YAML

```yaml
# core/primitives/asset_registry/mock-gbp.yaml
asset_id: mock-gbp
chain_id: ""
contract: "GBP"
decimals: 2
```

### Step 2 — Decide if an existing adapter already supports it

`MockSettlementAdapter.supports()` just checks membership in the tuple
its constructor was passed. That means you DON'T need a new adapter
class for a new mock asset — you just construct the mock with the new
AssetRef in its supported tuple:

```python
mock = MockSettlementAdapter(supported_assets=(usd, gbp))
```

If you *did* have a real adapter whose `supports()` is hard-coded (e.g.
the EVM adapter only claims `chain_id == "base-mainnet"`), you would
subclass it or extend its `supports()` to include the new asset. The
registry will refuse any overlap at `register()` time, so mistakes
surface loudly.

### Step 3 — Register it at node startup

```python
asset_reg = AssetRegistry()
asset_reg.load(Path("core/primitives/asset_registry"))
usd = asset_reg.get("mock-usd")
gbp = asset_reg.get("mock-gbp")

adapters = AdapterRegistry(asset_reg)
adapters.register(MockSettlementAdapter(supported_assets=(usd, gbp)))

# Dispatch works by asset:
print(adapters.adapter_for(gbp).supports(gbp))  # True
```

That's the whole drill — one YAML, one adapter constructor argument,
one `register()` call. No module-level globals, no cross-node wiring.

## 5. Runnable doctest example

```python
>>> from pathlib import Path
>>> from core.primitives import (
...     AssetRegistry, AdapterRegistry, MockSettlementAdapter,
... )
>>> reg = AssetRegistry()
>>> reg.load(Path("core/primitives/asset_registry")) >= 2
True
>>> usd = reg.get("mock-usd")
>>> usd.decimals
6
>>> adapters = AdapterRegistry(reg)
>>> adapters.register(MockSettlementAdapter(supported_assets=(usd,)))
>>> adapters.adapter_for(usd).supports(usd)
True
```

This block is executed verbatim by `tests/test_settlement_docs.py`.
