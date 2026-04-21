"""
core/primitives/asset.py — AssetRef + AssetRegistry
===================================================
Currency-agnostic asset primitive for the settlement architecture
(Ticket 1 of the currency-agnostic plan).

Each asset is described by a small YAML file in
`core/primitives/asset_registry/` and exposed to the rest of the
system as a frozen `AssetRef` dataclass. The `AssetRegistry` class
mirrors the YAML-loader pattern in `core.skill_registry`.

Deliberate non-goals:
- NO module-level singleton. Every caller (tests, adapters) builds
  its own `AssetRegistry` and calls `.load(root)`. This keeps test
  isolation clean and avoids hidden global state.
- NO network access at load time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# AssetRef — the primitive
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AssetRef:
    """Immutable reference to a single asset.

    `asset_id` is the registry key (e.g. "mock-usd", "usdc-base").
    `chain_id` is the chain identifier for on-chain assets (e.g.
    "base-mainnet") or the empty string for mock/off-chain entries.
    `contract` is the contract address for on-chain assets, or a
    ticker/symbol for mocks. `decimals` is the precision used by
    the `Money` primitive for quantization.
    """

    asset_id: str
    chain_id: str = ""
    contract: str = ""
    decimals: int = 6

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: Path | None = None) -> "AssetRef":
        """Build an AssetRef from a parsed YAML mapping.

        Raises ValueError with the source path when required fields
        are missing, so loader error messages point straight at the
        offending file.
        """
        loc = f" in {source}" if source is not None else ""
        if "asset_id" not in data:
            raise ValueError(f"asset YAML missing required field 'asset_id'{loc}")
        return cls(
            asset_id=str(data["asset_id"]),
            chain_id=str(data.get("chain_id", "")),
            contract=str(data.get("contract", "")),
            decimals=int(data.get("decimals", 6)),
        )


# ---------------------------------------------------------------------------
# AssetRegistry — YAML-backed in-memory index
# ---------------------------------------------------------------------------
class AssetRegistry:
    """In-memory index of available assets.

    `load(root)` walks `root/*.yaml`, parses each file, and registers
    the resulting `AssetRef` by `asset_id`. The registry owns its own
    state — independent instances never share a dict.
    """

    def __init__(self) -> None:
        self._assets: dict[str, AssetRef] = {}
        self._loaded: bool = False

    def load(self, root: Path | None = None) -> int:
        """Walk `root/*.yaml`, register each as an AssetRef.

        Returns the number of NEW assets registered on this call.
        A missing `root` directory returns 0. Malformed YAML (parse
        error or missing required fields) raises ValueError with the
        offending file path in the message.
        """
        if root is None:
            raise ValueError(
                "AssetRegistry.load requires an explicit root Path — "
                "no module-level singleton exists."
            )
        root = Path(root)
        self._loaded = True
        if not root.exists() or not root.is_dir():
            return 0

        added = 0
        for path in sorted(root.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ValueError(
                    f"asset YAML parse failed at {path}: {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise ValueError(
                    f"asset YAML at {path} must be a mapping, "
                    f"got {type(data).__name__}"
                )
            ref = AssetRef.from_dict(data, source=path)
            if ref.asset_id not in self._assets:
                added += 1
            self._assets[ref.asset_id] = ref
        return added

    def get(self, asset_id: str) -> AssetRef:
        """Return the AssetRef for `asset_id`, or raise KeyError."""
        try:
            return self._assets[asset_id]
        except KeyError as exc:
            raise KeyError(f"asset {asset_id!r} not found") from exc

    def ids(self) -> list[str]:
        """Return the sorted list of registered asset IDs."""
        return sorted(self._assets.keys())
