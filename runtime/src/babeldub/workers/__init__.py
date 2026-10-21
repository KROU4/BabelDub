"""Engine workers and the CLI-side client.

`client.py` runs inside the CLI. Every other module here is a standalone script executed by
an engine venv (`<env python> -u <worker>.py`); those scripts never import babeldub and talk
JSONL over stdin/stdout (contract §6).
"""
