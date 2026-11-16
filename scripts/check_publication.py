"""Scan staged Git blobs, not working copies. No third-party dependencies."""
import re
import sys
import subprocess
from pathlib import PurePosixPath

PATTERNS = [
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),
    re.compile(r'(?im)^\s*[\"\x27]?(?:api[_-]?(?:key|secret)|password|passphrase|access[_-]?token|bot[_-]?token)[\"\x27]?\s*[:=]\s*[\"\x27]?[^\s\"\x27]{8,}'),
    re.compile(r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b'),
]

def issues(name, content):
    p = PurePosixPath(name)
    blocked = (p.name.startswith('.env') or p.suffix.lower() in {'.pem', '.key', '.db', '.sqlite', '.log'}
               or any(x in {'private', 'backups', 'logs', 'data'} for x in p.parts))
    errors = ['blocked path'] if blocked else []
    if len(content) > 1024 * 1024:
        errors.append('file exceeds review limit')
    if b'\x00' in content:
        errors.append('binary content requires separate review')
    text = content.decode('utf-8', errors='replace')
    if any(p.search(text) for p in PATTERNS):
        errors.append('possible credential; value not printed')
    return errors

def main():
    all_files = '--all' in sys.argv
    command = ['git', 'ls-files', '-z'] if all_files else ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z']
    names = subprocess.check_output(command).decode().split('\x00')
    names = [n for n in names if n]
    failures = []
    for name in names:
        blob = subprocess.check_output(['git', 'show', ':' + name])
        failures.extend((name, reason) for reason in issues(name, blob))
    for name, reason in failures:
        print(name + ': ' + reason)
    print('staged_files=' + str(len(names)) + ' issues=' + str(len(failures)))
    return bool(failures)

if __name__ == '__main__':
    raise SystemExit(main())
