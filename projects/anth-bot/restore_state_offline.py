#!/usr/bin/env python3
"""Review a polluted ANTH state. Default is dry-run and writes nothing.

    python3 restore_state_offline.py current.json backup.json official.json window.json
    python3 restore_state_offline.py current.json backup.json official.json window.json --output proposal.json

The official file is the authorized read-only collection, not a typed quantity.
A source string is not a signature. The command refuses network access and refuses
to run beside a .env file. It never overwrites an input, never writes state.json,
and never deletes an intent. Exit 0 prints a proposal. Exit 2 refuses.
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
    raise SystemExit("refuse: .env is beside this tool; copy bot.py and the JSON files to an empty directory")

sys.path.insert(0, str(HERE))
import bot  # noqa: E402


def load(path):
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError("not-object")
    return payload


def refuse(reason):
    json.dump(dict(ok=False, wrote_inputs=False, applied_to_input=False, loadable_state=False, reason=reason), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 2


def main(argv):
    args = list(argv[1:])
    output = None
    if "--output" in args:
        slot = args.index("--output")
        if slot + 1 >= len(args):
            return refuse("usage: restore_state_offline.py current.json backup.json official.json window.json [--output proposal.json]")
        output = args[slot + 1]
        del args[slot:slot + 2]
    if len(args) != 4:
        return refuse("usage: restore_state_offline.py current.json backup.json official.json window.json [--output proposal.json]")
    try:
        current, backup, official, window = (load(path) for path in args)
    except (OSError, json.JSONDecodeError, ValueError):
        return refuse("refuse: each file must be one JSON object, not a typed number or list")
    proposal = bot.propose_polluted_state(current, backup, official, window)
    proposal["proposal_file_written"] = False
    proposal["dry_run"] = True
    if output is None:
        json.dump(proposal, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0 if proposal.get("ok") else 2
    if not proposal.get("ok"):
        json.dump(proposal, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 2
    destination = Path(output)
    if destination.exists():
        return refuse("refuse: output already exists")
    if destination.name == "state.json":
        return refuse("refuse: output must not be named state.json")
    inputs = [Path(path).resolve() for path in args]
    if destination.resolve() in inputs:
        return refuse("refuse: output path is an input path")
    proposal["proposal_file_written"] = True
    proposal["dry_run"] = False
    text = "ANTH_RESTORE_PROPOSAL not a loadable state\n" + json.dumps(proposal, ensure_ascii=False, indent=2) + "\n"
    destination.write_text(text)
    json.dump(dict(ok=proposal.get("ok"), dry_run=False, proposal_file_written=True, output=str(destination),
                   wrote_inputs=False, applied_to_input=False, loadable_state=False,
                   restored_ids=proposal.get("restored_ids"), marker=proposal.get("marker")), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if proposal.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
