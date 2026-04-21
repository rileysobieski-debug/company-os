"""Company OS CLI package (Phase 13.3).

Entry point: `python -m cli <subcommand>` or (after packaging) `companyos <subcommand>`.

Subcommands wire into existing core primitives — this layer is thin on purpose.
Adding a new subcommand means adding an `_add_<name>_parser()` helper and a
`cmd_<name>()` handler in `cli.main`.
"""
