"""Small, explicit source-pattern check. Not a replacement for a full security audit."""

import json
import re
from pathlib import Path

PATTERN = re.compile(
    r"(?:sk-proj-|ghp_|github_pat_)[A-Za-z0-9_]{20,}"
    r"|cip_[A-Za-z0-9_-]{40,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


def run() -> dict:
    paths = [
        p
        for root in ("src", "tests", "scripts", "docs", "examples", ".github")
        for p in Path(root).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    paths.extend(Path(p) for p in ("README.md", "pyproject.toml", "Dockerfile"))
    findings = []
    for path in paths:
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            if PATTERN.search(line):
                findings.append({"file": str(path), "line": line_no})
    return {
        "check": "known_credential_and_private_key_patterns",
        "files": len(paths),
        "findings": findings,
        "passed": not findings,
        "limit": "Limited source-pattern check; values are never printed",
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
