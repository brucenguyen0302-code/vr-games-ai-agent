#!/usr/bin/env python3
"""
scripts/check_secrets.py
-------------------------
Scans every git-tracked file for API-key-like strings before it lands in
history. Intended to be run in CI and/or as a pre-commit/pre-push hook.

Detects:
  - Google API keys                  (AIza...)
  - Hugging Face tokens               (hf_...)
  - Meta / Facebook Graph API tokens  (EAA...)
  - Generic "key/token/secret/password = <long string>" assignments

Exits with status 1 (and prints every match found, as file:line) if
anything looks like a leaked credential; exits 0 otherwise.

Usage
-----
    python scripts/check_secrets.py
"""

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# (label, compiled pattern)
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Google API key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("Hugging Face token", re.compile(r"hf_[0-9A-Za-z]{20,}")),
    ("Meta/Facebook access token", re.compile(r"EAA[0-9A-Za-z]{20,}")),
    (
        "Generic secret assignment",
        re.compile(
            r"(?i)(api[_-]?key|access[_-]?token|secret|password)"
            r"\s*[:=]\s*['\"][0-9A-Za-z\-_]{16,}['\"]"
        ),
    ),
]

# This file legitimately contains the pattern source text above (as string
# literals, not real keys) — never flag itself.
_EXCLUDE_PATHS = {"scripts/check_secrets.py"}
_EXCLUDE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".db", ".pdf"}


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _scan_file(rel_path: str) -> list[str]:
    path = PROJECT_ROOT / rel_path
    if path.suffix.lower() in _EXCLUDE_SUFFIXES or not path.is_file():
        return []

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    findings = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(f"{rel_path}:{line_no}: possible {label}")
    return findings


def main() -> int:
    findings: list[str] = []
    for rel_path in _tracked_files():
        if rel_path in _EXCLUDE_PATHS:
            continue
        findings.extend(_scan_file(rel_path))

    if findings:
        print("Potential secrets found in tracked files:\n")
        for f in findings:
            print(f"  {f}")
        print(
            "\nIf these are real credentials: remove them, rotate the secret "
            "immediately, and load it from an environment variable instead. "
            "If this is a false positive, adjust scripts/check_secrets.py."
        )
        return 1

    print("No secrets detected in tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
