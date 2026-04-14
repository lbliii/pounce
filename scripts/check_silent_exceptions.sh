#!/usr/bin/env bash
# CI check: flag bare exception swallowing that ruff S110 can't catch.
# Patterns flagged:
#   1. contextlib.suppress(Exception) without a # silent: reason
#   2. broad except (Exception/bare except) with continue and no logging
#
# Narrow exceptions (TimeoutError, queue.Empty, OSError, etc.) used for
# control flow in poll loops are NOT flagged — only broad catches.
#
# Exit 0 = clean, exit 1 = violations found.

set -euo pipefail

ROOT="${1:-src/pounce}"
VIOLATIONS=0

# Regex that recognises logging calls used in this codebase
LOG_RE='(log\.|logger\.|_logger\.|self\._logger\.|logging\.|raise |# silent:)'

# --- 1. contextlib.suppress(Exception) without # silent: annotation ----------
while IFS= read -r match; do
    if [[ -n "$match" ]]; then
        echo "ERROR: contextlib.suppress(Exception) without '# silent:' comment:"
        echo "  $match"
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done < <(grep -rn 'contextlib\.suppress(Exception)' "$ROOT" \
    --include='*.py' \
    | grep -v '# silent:' \
    | grep -v '_bench\.py' \
    || true)

# --- 2. broad except + continue without logging ------------------------------
# Matches: except Exception: / except Exception as e: / except: (bare)
# followed by continue (with optional trailing comment)
_check_except_continue() {
    local pattern="$1"
    local label="$2"
    while IFS=: read -r file lineno rest; do
        if [[ -z "$file" ]]; then continue; fi
        except_line=$(sed -n "${lineno}p" "$file")
        # Skip if annotated
        if echo "$except_line" | grep -qE '# silent:'; then continue; fi
        # Look at the next few lines for logging/raise
        next_lines=$(sed -n "$((lineno + 1)),$((lineno + 3))p" "$file")
        if echo "$next_lines" | grep -qE "$LOG_RE"; then continue; fi
        echo "ERROR: ${label}:"
        echo "  ${file}:${lineno}: ${rest}"
        VIOLATIONS=$((VIOLATIONS + 1))
    done < <(grep -rn -E "$pattern" "$ROOT" \
        --include='*.py' \
        | while IFS=: read -r f l r; do
            next_line=$((l + 1))
            next=$(sed -n "${next_line}p" "$f" 2>/dev/null || echo "")
            if echo "$next" | grep -qE '^\s*continue\s*(#.*)?$'; then
                echo "${f}:${l}:${r}"
            fi
        done || true)
}

_check_except_continue 'except\s+Exception(\s+as\s+\w+)?\s*:' "broad except-continue without logging"
_check_except_continue 'except\s*:' "bare except-continue without logging"

if [[ $VIOLATIONS -gt 0 ]]; then
    echo ""
    echo "Found $VIOLATIONS silent-exception violation(s)."
    echo "Fix: add logging, re-raise, or annotate with '# silent: <reason>'"
    exit 1
fi

echo "No silent-exception violations found."
