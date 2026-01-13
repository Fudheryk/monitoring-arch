#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# ---------------------------
# Config (ajuste si besoin)
# ---------------------------
DEFAULT_ROOT = Path(".")
INCLUDE_DIRS = {"app", "tests"}  # scanne aussi tests si tu veux
EXCLUDE_DIR_PARTS = {
    ".venv", "venv", "__pycache__", ".git", ".mypy_cache", ".pytest_cache",
    "node_modules", "dist", "build",
}

# Détection (regex simples mais robustes)
RE_OPEN_CALL = re.compile(r"\b(?:irepo|repo)\.open\s*\(", re.MULTILINE)
RE_OPEN_HTTP_CHECK_CALL = re.compile(r"\bopen_http_check\s*\(", re.MULTILINE)
RE_INCIDENT_CTOR = re.compile(r"\bIncident\s*\(", re.MULTILINE)

# Trouver `incident_type=` dans la “fenêtre” d’appel
RE_HAS_INCIDENT_TYPE_KW = re.compile(r"\bincident_type\s*=", re.MULTILINE)

# (optionnel) ignorer les faux positifs dans certains fichiers
IGNORE_PATH_CONTAINS = {
    # ex: "migrations",  # si tu ne veux pas scanner migrations
}

# ---------------------------
# Utilitaires
# ---------------------------

@dataclass(frozen=True)
class Finding:
    kind: str
    path: Path
    line: int
    snippet: str


def iter_py_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        # prune exclusions
        parts = set(Path(dirpath).parts)
        if parts & EXCLUDE_DIR_PARTS:
            dirnames[:] = []
            continue

        # prune includes (si on est à la racine)
        # On garde si un parent contient INCLUDE_DIRS (souple)
        # ou si root lui-même est un sous-projet.
        if root == Path("."):
            if not (parts & INCLUDE_DIRS):
                # laisser descendre jusqu'à trouver app/tests
                pass

        for fn in filenames:
            if fn.endswith(".py"):
                p = Path(dirpath) / fn
                # filtre soft : uniquement app/tests si présents
                if INCLUDE_DIRS:
                    if not (set(p.parts) & INCLUDE_DIRS):
                        continue
                if any(seg in p.parts for seg in IGNORE_PATH_CONTAINS):
                    continue
                yield p


def line_number_from_index(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def extract_window(text: str, start_idx: int, max_chars: int = 600) -> str:
    # prend une fenêtre à partir de l’appel, jusqu’à max_chars
    return text[start_idx : min(len(text), start_idx + max_chars)]


def first_line_snippet(window: str, max_len: int = 200) -> str:
    s = window.splitlines()[0] if window else ""
    s = s.strip()
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


def find_missing_incident_type_in_calls(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []

    # 1) repo.open(...) sans incident_type=
    for m in RE_OPEN_CALL.finditer(text):
        win = extract_window(text, m.start())
        if not RE_HAS_INCIDENT_TYPE_KW.search(win):
            findings.append(
                Finding(
                    kind="MISSING incident_type in IncidentRepository.open",
                    path=path,
                    line=line_number_from_index(text, m.start()),
                    snippet=first_line_snippet(win),
                )
            )

    # 2) open_http_check(...) sans incident_type= (si tu le passes)
    #    Si tu as décidé de le forcer DANS le repo, tu peux ignorer ce check.
    for m in RE_OPEN_HTTP_CHECK_CALL.finditer(text):
        win = extract_window(text, m.start())
        if not RE_HAS_INCIDENT_TYPE_KW.search(win):
            findings.append(
                Finding(
                    kind="CHECK open_http_check missing incident_type (if required by signature)",
                    path=path,
                    line=line_number_from_index(text, m.start()),
                    snippet=first_line_snippet(win),
                )
            )

    return findings


def find_direct_incident_ctor_without_type(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for m in RE_INCIDENT_CTOR.finditer(text):
        win = extract_window(text, m.start())

        # Si le ctor est dans incident_repository.py, c’est peut-être volontaire.
        # Ici on remonte quand même, mais on tag “direct ctor”.
        if not RE_HAS_INCIDENT_TYPE_KW.search(win):
            findings.append(
                Finding(
                    kind="DIRECT Incident(...) without incident_type",
                    path=path,
                    line=line_number_from_index(text, m.start()),
                    snippet=first_line_snippet(win),
                )
            )
    return findings


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROOT
    root = root.resolve()

    if not root.exists():
        print(f"Root not found: {root}")
        return 2

    all_findings: list[Finding] = []

    for p in iter_py_files(root):
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # fallback
            text = p.read_text(errors="ignore")

        all_findings.extend(find_missing_incident_type_in_calls(p, text))
        all_findings.extend(find_direct_incident_ctor_without_type(p, text))

    if not all_findings:
        print("✅ OK: no missing incident_type detected in incident openings / Incident() ctors.")
        return 0

    # Sort stable
    all_findings.sort(key=lambda f: (str(f.path), f.line, f.kind))

    print("❌ Findings (potential missing incident_type):")
    print("-" * 80)
    for f in all_findings:
        rel = f.path.relative_to(root) if f.path.is_relative_to(root) else f.path
        print(f"[{f.kind}] {rel}:{f.line}")
        print(f"  {f.snippet}")
    print("-" * 80)

    # Summary by kind
    by_kind: dict[str, int] = {}
    for f in all_findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1

    print("Summary:")
    for k, n in sorted(by_kind.items(), key=lambda x: (-x[1], x[0])):
        print(f"  - {k}: {n}")

    # Non-zero: good for CI
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
