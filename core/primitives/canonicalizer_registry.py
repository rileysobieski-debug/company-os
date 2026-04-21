"""
core/primitives/canonicalizer_registry.py -- CanonicalizerRegistry primitive
=============================================================================
Ticket B0-a of the v1b Oracle build. Provides a version-keyed registry for
canonicalization functions so that `OracleVerdict` (and future signed types)
can correctly derive canonical bytes for ANY historical protocol version.

Design principle (per ruling 20)
---------------------------------
The `protocol_version` key inside a signed shell dict is PROTOCOL-CONSTANT:
its location and parse logic never change across wire-format versions. It is
always read BEFORE invoking any canonicalizer. This is deliberately separated
from the canonicalization step, which IS protocol-varying.

In other words:
  - "The version parse is protocol-constant."
  - "The canonicalizer is protocol-varying."

`extract_protocol_version` encodes the invariant pre-parse step.
`CanonicalizerRegistry.get` encodes the protocol-varying dispatch step.

Circular-import note
---------------------
This module does NOT import from `core.primitives.oracle`. The registry
singleton is created empty here. `core.primitives.oracle` registers its own
canonicalizer at module-load time (after defining `_canonical_bytes`). This
keeps the dependency edge in one direction: oracle -> registry, never the
reverse.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

# Type alias for a canonicalization function.
# Args:
#   shell            -- the verdict (or signed-object) shell dict with
#                       excluded fields already removed by the caller.
#   exclude_verdict_hash -- when True, the "verdict_hash" key is omitted
#                       from the bytes input (used during hash computation).
# Returns:
#   canonical UTF-8 bytes suitable for hashing or signing.
CanonicalizerFn = Callable[[Mapping[str, Any], bool], bytes]


class CanonicalizerRegistry:
    """Version-keyed registry mapping protocol_version strings to canonicalization
    functions.

    Each canonicalization function produces the canonical bytes for a signed
    object under the rules of its protocol version. Registering a new version
    does not affect any existing registered version.

    Usage:
        registry = CanonicalizerRegistry()
        registry.register("companyos-verdict/0.1", my_fn)
        fn = registry.get("companyos-verdict/0.1")
        body = fn(shell_dict, exclude_verdict_hash=False)
    """

    def __init__(self) -> None:
        self._registry: dict[str, CanonicalizerFn] = {}

    def register(self, version: str, fn: CanonicalizerFn) -> None:
        """Register a canonicalization function for a protocol version string.

        Overwrites any existing registration for `version` (last-write wins).
        This is intentional: tests and future migrations may need to replace a
        canonicalizer without restarting the process.

        Args:
            version: The protocol_version string, e.g. "companyos-verdict/0.1".
            fn:      A CanonicalizerFn matching the signature
                     ``(shell: Mapping[str, Any], exclude_verdict_hash: bool)
                     -> bytes``.

        Raises:
            TypeError: if `version` is not a str or `fn` is not callable.
        """
        if not isinstance(version, str):
            raise TypeError(
                f"version must be a str, got {type(version).__name__}"
            )
        if not callable(fn):
            raise TypeError(
                f"fn must be callable, got {type(fn).__name__}"
            )
        self._registry[version] = fn

    def get(self, version: str) -> CanonicalizerFn:
        """Return the canonicalization function for the given protocol version.

        Args:
            version: The protocol_version string to look up.

        Returns:
            The registered CanonicalizerFn for that version.

        Raises:
            ValueError: if `version` has not been registered.
        """
        fn = self._registry.get(version)
        if fn is None:
            registered = sorted(self._registry.keys())
            raise ValueError(
                f"no canonicalizer registered for protocol_version "
                f"{version!r}. Registered versions: {registered}"
            )
        return fn


def extract_protocol_version(
    shell: Mapping[str, Any],
    *,
    default: str | None = None,
) -> str:
    """Read the `protocol_version` key from a shell dict.

    This is the PROTOCOL-CONSTANT pre-parse step that must run before any
    canonicalizer is invoked (per ruling 20). The version key location and
    its read logic are invariant across ALL protocol versions: no matter what
    the canonicalizer does with the rest of the dict, this extraction step
    always runs identically.

    The canonicalizer IS protocol-varying and is selected AFTER this call.

    Args:
        shell:   The shell dict (verdict or other signed object) to inspect.
        default: Optional fallback returned when the key is absent. If not
                 supplied and the key is missing, ValueError is raised.

    Returns:
        The protocol_version string found in `shell`, or `default` if the key
        is absent and `default` was provided.

    Raises:
        ValueError: if `protocol_version` is absent from `shell` and no
                    `default` was supplied.
    """
    version = shell.get("protocol_version")
    if version is None:
        if default is not None:
            return default
        raise ValueError(
            "shell dict is missing required key 'protocol_version'. "
            "This key must be present before any canonicalizer can be selected."
        )
    return str(version)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
# Created empty. `core.primitives.oracle` registers the v1a canonicalizer
# ("companyos-verdict/0.1") at its own module-load time to avoid circular
# imports. Future signed types (e.g. Challenge in B1-c) register their own
# versions similarly.
default_canonicalizer_registry: CanonicalizerRegistry = CanonicalizerRegistry()

__all__ = [
    "CanonicalizerFn",
    "CanonicalizerRegistry",
    "default_canonicalizer_registry",
    "extract_protocol_version",
]
