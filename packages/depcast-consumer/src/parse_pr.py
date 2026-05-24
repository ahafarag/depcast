"""
Parse Renovate PR titles and bodies to extract (package, from_version, to_version).

Renovate title formats:
  Update dependency chalk to v5.0.0
  chore(deps): update dependency chalk to ^5.0.0
  Update dependency @types/node to v20.0.0
  Update dependency chalk from 4.1.2 to 5.0.0
  fix(deps): update dependency react to v18
  Update npm dependency chalk from 4 to 5

Returns None when the PR doesn't look like a Renovate upgrade PR.
"""

import re

# Strips range operators and 'v' prefix from a version string
_CLEAN_VER = re.compile(r'^[v^~>=<*]+')

# Main title patterns — most specific first
_PATTERNS = [
    # "Update dependency X from A to B"
    re.compile(
        r"(?:chore\(deps\):\s*)?update\s+(?:\w+\s+)?dependency\s+"
        r"(@?[\w\-./]+)\s+from\s+([\d.]+)\s+to\s+[v^~>=<*]*([\d.]+)",
        re.IGNORECASE,
    ),
    # "Update dependency X to vB"
    re.compile(
        r"(?:chore\(deps\):\s*)?update\s+(?:\w+\s+)?dependency\s+"
        r"(@?[\w\-./]+)\s+to\s+[v^~>=<*]*([\d.]+(?:\.\d+)*)",
        re.IGNORECASE,
    ),
    # "Update X to vB" (no 'dependency' keyword)
    re.compile(
        r"(?:chore\(deps\):\s*)?update\s+(@?[\w\-./]+)\s+to\s+[v^~>=<*]*([\d.]+(?:\.\d+)*)",
        re.IGNORECASE,
    ),
]


def parse_renovate_title(title: str) -> dict | None:
    """
    Returns {"package": str, "to_version": str, "from_version": str | None}
    or None if the title doesn't match a Renovate upgrade pattern.
    """
    title = title.strip()

    # Three-group pattern: package, from, to
    m = _PATTERNS[0].search(title)
    if m:
        return {
            "package": m.group(1),
            "from_version": _clean(m.group(2)),
            "to_version": _clean(m.group(3)),
        }

    # Two-group patterns: package, to
    for pat in _PATTERNS[1:]:
        m = pat.search(title)
        if m:
            return {
                "package": m.group(1),
                "from_version": None,
                "to_version": _clean(m.group(2)),
            }

    return None


def _clean(v: str) -> str:
    return _CLEAN_VER.sub("", v).strip()


# ── Tests (run directly) ───────────────────────────────────────────────────────
if __name__ == "__main__":
    cases = [
        ("Update dependency chalk to v5.0.0",
         {"package": "chalk", "from_version": None, "to_version": "5.0.0"}),
        ("chore(deps): update dependency chalk to ^5.0.0",
         {"package": "chalk", "from_version": None, "to_version": "5.0.0"}),
        ("Update dependency @types/node to v20.11.0",
         {"package": "@types/node", "from_version": None, "to_version": "20.11.0"}),
        ("Update dependency chalk from 4.1.2 to 5.0.0",
         {"package": "chalk", "from_version": "4.1.2", "to_version": "5.0.0"}),
        ("fix(deps): update dependency react to v18",
         {"package": "react", "from_version": None, "to_version": "18"}),
        ("Update npm dependency lodash from 4.17.15 to 4.17.21",
         {"package": "lodash", "from_version": "4.17.15", "to_version": "4.17.21"}),
        ("Bump chalk from 4.1.2 to 5.0.0", None),  # not Renovate format
    ]
    ok = True
    for title, expected in cases:
        got = parse_renovate_title(title)
        status = "OK" if got == expected else "FAIL"
        if status == "FAIL":
            ok = False
        print(f"[{status}] {title!r}")
        if status == "FAIL":
            print(f"  expected: {expected}")
            print(f"  got:      {got}")
    print("\nAll OK" if ok else "\nSome FAILED")
