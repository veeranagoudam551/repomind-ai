"""Basic defensive security scanning (architecture.md Phase 4).

Deliberately not LLM-backed: this is a small, fixed set of regex rules
for well-known insecure patterns (hardcoded secrets, eval/exec, shell
injection, insecure deserialization, unsafe YAML loading, SQL built via
string interpolation, unsanitized HTML injection). "Basic" is the
point - it flags patterns worth a human look, not a full understanding
of the code, and unlike every other AI feature in this app it needs no
provider API key at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_SNIPPET_LENGTH = 200

# Day 58: max_file_size_kb (app/core/config.py) bounds a scanned file's
# *total* size, but not any single line's length - a pathological file
# with one extremely long line (a minified bundle, a data blob with no
# newlines) would otherwise still hand that whole line to every compiled
# regex below. 2000 characters comfortably covers realistic source lines
# (long import lists, long URLs, generated code) while keeping each rule's
# per-line work bounded regardless of how one file happens to be laid out.
MAX_LINE_LENGTH = 2000


@dataclass(frozen=True)
class SecurityRule:
    rule_id: str
    severity: str
    pattern: re.Pattern[str]
    message: str


@dataclass(frozen=True)
class SecurityFinding:
    line: int
    rule_id: str
    severity: str
    message: str
    snippet: str


_RULES: list[SecurityRule] = [
    SecurityRule(
        "hardcoded-secret",
        "high",
        re.compile(
            # \w* on both sides so prefixed/suffixed identifiers like
            # DB_PASSWORD or MY_API_KEY match too, not just bare names.
            r"""(?i)\b\w*(?:api[_-]?key|secret[_-]?key|access[_-]?token|password|passwd|pwd)\w*"""
            r"""\s*[:=]\s*["'][A-Za-z0-9_\-/+=]{8,}["']"""
        ),
        "Possible hardcoded credential or secret",
    ),
    SecurityRule(
        "private-key",
        "high",
        re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
        "Private key material committed to source",
    ),
    SecurityRule(
        "eval-exec",
        "high",
        re.compile(r"\b(eval|exec)\s*\("),
        "Use of eval()/exec() on potentially untrusted input",
    ),
    SecurityRule(
        "shell-injection",
        "high",
        re.compile(r"\bos\.system\s*\(|subprocess\.\w+\([^)]*shell\s*=\s*True"),
        "Shell command execution via os.system() or shell=True",
    ),
    SecurityRule(
        "insecure-deserialization",
        "medium",
        re.compile(r"\bpickle\.loads?\s*\("),
        "Deserializing with pickle can execute arbitrary code",
    ),
    SecurityRule(
        "unsafe-yaml-load",
        "medium",
        re.compile(r"\byaml\.load\s*\((?!.*Loader)"),
        "yaml.load() without a safe Loader can execute arbitrary code",
    ),
    SecurityRule(
        "sql-string-interpolation",
        "medium",
        re.compile(
            r"""f["'][^"']*\b(SELECT|INSERT|UPDATE|DELETE)\b[^"']*\{""", re.IGNORECASE
        ),
        "SQL built via string interpolation is vulnerable to injection",
    ),
    SecurityRule(
        "unsafe-html-injection",
        "medium",
        re.compile(r"\.innerHTML\s*=|dangerouslySetInnerHTML"),
        "Setting raw HTML can lead to XSS if the content isn't sanitized",
    ),
]


def scan_content(content: str) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        bounded_line = line[:MAX_LINE_LENGTH]
        for rule in _RULES:
            if rule.pattern.search(bounded_line):
                findings.append(
                    SecurityFinding(
                        line=line_number,
                        rule_id=rule.rule_id,
                        severity=rule.severity,
                        message=rule.message,
                        snippet=bounded_line.strip()[:MAX_SNIPPET_LENGTH],
                    )
                )
    return findings
