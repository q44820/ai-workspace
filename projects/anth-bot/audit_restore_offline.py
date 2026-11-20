#!/usr/bin/env python3
"""Print one restore proposal. This process does not write state and does not trade.

Usage, from an empty directory that contains this file, bot.py, and no .env:

    python3 audit_restore_offline.py current.json backup.json official.json

official.json is the ops read-only query record. A hand-typed quantity is not
that record. Required fields: source (official-order or official-submission),
official_id, venue (io or lt), filled. Optional: state, oid.

The command refuses when any input is missing, the three records disagree, the
current row is not unknown, or the current row already has official evidence.
Exit 0 prints a reduced proposal. Exit 2 prints a refusal. Neither exit writes
a file or opens a socket.
"""
import json
import socket
import sys
from pathlib import Path


def _refuse_network(*_args, **_kwargs):
    raise RuntimeError("offline restore tool refuses network")


socket.socket.connect = _refuse_network
socket.create_connection = _refuse_network

HERE = Path(__file__).resolve().parent
if (HERE / ".env").exists():
    raise SystemExit("refuse: .env is beside this tool; copy bot.py and the three JSON files to an empty directory")

sys.path.insert(0, str(HERE))
import bot  # noqa: E402


def load(path):
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError("not-object")
    return payload


def refuse(reason):
    json.dump(dict(ok=False, wrote=False, applied=False, reason=reason), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 2


def main(argv):
    if len(argv) != 4:
        return refuse("usage: python3 audit_restore_offline.py current.json backup.json official.json")
    try:
        current, backup, official = load(argv[1]), load(argv[2]), load(argv[3])
    except (OSError, json.JSONDecodeError, ValueError):
        return refuse("refuse: each file must be one JSON object, not a typed number or list")
    proposed = bot.audit_restore_intent(current, backup, official)
    if proposed is None:
        return refuse("refuse: records do not meet the restore preconditions")
    evidence = proposed.get("terminal_evidence") or {}
    json.dump(dict(
        ok=True, wrote=False, applied=False,
        official_id=proposed.get("official_id"), venue=proposed.get("venue"), status=proposed.get("status"),
        terminal_evidence=dict(source=evidence.get("source"), filled=evidence.get("filled"), state=evidence.get("state"), oid=evidence.get("oid")),
        audit_note=proposed.get("audit_note"),
    ), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
