# node_registry/

On-disk YAML store of DID → Ed25519 pubkey bindings. One entry per file.
Filenames are derived from the DID via `sha256(did)[:12].yaml` so they
are filesystem-safe on Windows (raw DIDs contain colons, which are
illegal in Windows filenames). See `core/primitives/node_registry.py`.

Each file has shape:

```yaml
node_did: did:companyos:abc123
public_key_hex: "<64 hex chars>"
first_seen: "2026-04-19T12:00:00Z"
notes: "Old Press Wine Company primary node"
```

The registry indexes by the `node_did` value inside the file, so the
filename is a derived detail — renaming the file on disk will NOT
rebind the DID.
