"""Suite-wide guard against test-order dependencies.

Command modules bind helpers by name (`from babeldub.qa.qc import qc_missing` in commands/cast.py), and
`cli.main` imports them lazily on its first call. A test that monkeypatches `babeldub.qa.qc.qc_missing`
and only then calls `cli.main` would make the first import happen under the patch, freezing the fake into
`babeldub.commands.cast` for the rest of the session (the end-to-end test then asks a qc worker that is
not there). Importing every command module here, before any test runs, keeps those names bound to the
real functions regardless of the test order.
"""

from __future__ import annotations

from importlib import import_module

from babeldub.cli import COMMAND_MODULES

for _module_name in COMMAND_MODULES:
    import_module(_module_name)
