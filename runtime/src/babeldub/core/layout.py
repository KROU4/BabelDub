"""Relative paths of artifacts inside a project folder (single source of truth)."""

from __future__ import annotations

PROJECT_JSON = "project.json"
PROGRESS = "state/progress.json"
EVENTS = "state/events.jsonl"
GPU_LOCK = "state/locks/gpu.lock"
LOGS_DIR = "logs"

RECON = "inventory/recon.json"
ASSETS = "inventory/assets.jsonl"
LINES = "inventory/lines.jsonl"
TEXTS = "inventory/texts.jsonl"
INVENTORY_REPORT = "inventory/report.md"
ORIG_AUDIO_DIR = "audio/orig"

CHARACTERS = "cast/characters.jsonl"
ASSIGN = "cast/assign.jsonl"
REFS = "cast/refs.jsonl"
REFS_DIR = "cast/refs"
CASTING_SHEET = "cast/casting_sheet.md"
CAST_LABELS_AGENT = "cast/labels.agent.jsonl"

GLOSSARY = "translate/glossary.jsonl"
GLOSSARY_AGENT = "translate/glossary.agent.jsonl"
STYLE_BIBLE = "translate/style_bible.md"
BATCHES_DIR = "translate/batches"
TRANSLATIONS = "translate/translations.jsonl"

SYNTH_RESULTS = "synth/results.jsonl"
SYNTH_DIR = "synth/raw"
POST = "post/post.jsonl"
POST_DIR = "post/audio"

QA = "qa/qa.jsonl"
DECISIONS = "qa/decisions.jsonl"
DECISIONS_AGENT = "qa/decisions.agent.jsonl"
QA_REPORT = "qa/report.md"

BUILD_DIR = "build/out"
BUILD_MANIFEST = "build/build_manifest.json"
BACKUP_DIR = "build/backup"
INSTALL_LOG = "build/install.json"

STAGES = [
    "init",
    "scan",
    "extract",
    "link",
    "cast",
    "translate",
    "synth",
    "post",
    "qa",
    "build",
    "verify",
    "install",
]
