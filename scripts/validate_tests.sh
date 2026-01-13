#!/usr/bin/env bash
set -Eeuo pipefail

has_cmd() { command -v "$1" >/dev/null 2>&1; }

# Choisir une commande pytest valide
PYTEST=""
if has_cmd pytest; then
  PYTEST="pytest"
elif has_cmd python3 && python3 -c "import pytest" 2>/dev/null; then
  PYTEST="python3 -m pytest"
elif has_cmd python && python -c "import pytest" 2>/dev/null; then
  PYTEST="python -m pytest"
fi

errors=0

check_mark() {
  local subdir="$1" mark="$2"
  mapfile -d '' files < <(find "server/tests/${subdir}" -type f -name 'test_*.py' -print0 2>/dev/null || true)
  for f in "${files[@]:-}"; do
    if has_cmd rg; then
      if ! rg -q '(^\s*pytestmark\s*=\s*pytest\.mark\.'"$mark"')|(@pytest\.mark\.'"$mark"')' "$f"; then
        echo "MISSING MARK [$mark] -> $f"
        errors=$((errors+1))
      fi
    else
      if ! grep -Eq '(^[[:space:]]*pytestmark[[:space:]]*=[[:space:]]*pytest\.mark\.'"$mark"')|(@pytest\.mark\.'"$mark"')' "$f"; then
        echo "MISSING MARK [$mark] -> $f"
        errors=$((errors+1))
      fi
    fi
  done
}

# 1) Vérifier les marqueurs par dossier
for d in unit integration e2e; do
  [ -d "server/tests/$d" ] || continue
  check_mark "$d" "$d"
done

# 2) Détecter des tests à la racine de server/tests
root_tests=$(find server/tests -maxdepth 1 -type f -name 'test_*.py' -print || true)
if [ -n "${root_tests}" ]; then
  echo "TESTS AT ROOT OF server/tests (move them to unit/ integration/ e2e/):"
  echo "${root_tests}"
  errors=$((errors+1))
fi

# 3) __init__.py indésirables
inits=$(find server/tests -name '__init__.py' -print || true)
if [ -n "${inits}" ]; then
  echo "__init__.py files under server/tests (remove them):"
  echo "${inits}"
  errors=$((errors+1))
fi

# 4) Vérifier la collecte pytest (si dispo)
if [ -n "$PYTEST" ]; then
  if ! $PYTEST --collect-only -q >/dev/null; then
    echo "PYTEST COLLECTION FAILED"
    errors=$((errors+1))
  fi
else
  echo "pytest not found; skipping collection step. Activate venv and run:"
  echo "  source .venv/bin/activate && python -m pip install -e '.[dev]'"
fi

# 5) Résumé
if [ "$errors" -eq 0 ]; then
  echo "OK: tests tree and markers look good."
else
  echo "FAIL: detected $errors issue(s)."
  exit 1
fi
