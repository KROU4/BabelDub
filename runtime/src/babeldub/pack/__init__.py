"""Adapter-independent packing: build planning, container rebuild, verification, install and rollback.

planner   - accepted lines grouped by container, TTS models behind them, game fingerprint
builder   - `build` / `verify` orchestration and build/build_manifest.json
installer - sha1, backups, atomic swaps of game files, rollback, build/install.json
guards    - is the game running, is it protected by an anti-cheat
"""
