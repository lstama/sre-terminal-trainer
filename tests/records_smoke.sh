#!/bin/sh
# tests/records_smoke.sh - mode-aware records, hint counter and `best` filters.
#
# Builds src/verify.c, drives three throwaway trees through SRE_STATE_ROOT (no
# root needed): an Incident mode run, a Terminal mode run, and an old-format
# manifest with no MODE line. Then checks tools/best.py and `make best` filters.

set -u

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BIN=${1:-/tmp/sre-records-verify}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/sre-records.XXXXXX")
LOG=$WORK/build.log

trap 'rm -rf "$WORK"' EXIT INT TERM

pass=0
fail=0
OUT=""

ok()   { pass=$((pass + 1)); printf 'PASS  %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf 'FAIL  %s\n' "$1"; }
note() { printf '      %s\n' "$1"; }

expect_rc() {
    desc=$1; exp=$2; shift 2
    OUT=$("$@" 2>&1)
    rc=$?
    if [ "$rc" -eq "$exp" ]; then
        ok "$desc (rc=$rc)"
    else
        bad "$desc (expected rc=$exp, got $rc)"
        printf '%s\n' "$OUT" | sed 's/^/        | /'
    fi
}

# expect_contains <desc> <needle> [text]; defaults to the last command's output
expect_contains() {
    desc=$1; needle=$2; text=${3:-$OUT}
    if printf '%s\n' "$text" | grep -F -- "$needle" >/dev/null 2>&1; then
        ok "$desc"
    else
        bad "$desc (missing: $needle)"
        printf '%s\n' "$text" | sed 's/^/        | /'
    fi
}

expect_not_contains() {
    desc=$1; needle=$2; text=${3:-$OUT}
    if printf '%s\n' "$text" | grep -F -- "$needle" >/dev/null 2>&1; then
        bad "$desc (found forbidden: $needle)"
        printf '%s\n' "$text" | sed 's/^/        | /'
    else
        ok "$desc"
    fi
}

hexof() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }

printf '== build ==\n'
if ! gcc -O2 -static -Wall -Wextra -o "$BIN" \
        "$REPO_DIR/src/verify.c" "$REPO_DIR/src/sha256.c" "$REPO_DIR/src/util.c" \
        >"$LOG" 2>&1; then
    bad "static build failed"
    sed 's/^/        | /' "$LOG"
    exit 1
fi
ok "static build of src/verify.c"
if [ -s "$LOG" ]; then
    note "compiler diagnostics:"
    sed 's/^/        | /' "$LOG"
fi

# make_tree <root> <MODE-line-or-empty> <seed>
make_tree() {
    root=$1; mode_line=$2; tree_seed=$3
    mkdir -p "$root/state" "$root/stages/01"
    {
        [ -z "$mode_line" ] || printf '%s\n' "$mode_line"
        printf 'SEED %s\nSTAGES 1\nSTAGE recon\n' "$tree_seed"
    } >"$root/manifest"
    printf 'Find the passphrase.\n' >"$root/stages/01/instruction"
    printf 'Read the brief.\nCheck the logs.\n' >"$root/stages/01/hint"
    cat >"$root/stages/01/checks.tsv" <<EOF
id=h1
type=hash
answer=$(hexof one)
fragment=frag-one
hint=Try the obvious word

EOF
    TREE_FLAG="FLAG$(printf '{%s}' "$(hexof 'frag-one' | cut -c1-12)")"
    hexof "$TREE_FLAG" >"$root/stages/01/flag.sha256"
    hexof 'FLAG{t56-final}' >"$root/final.sha256"
}

# solve_tree <runner-name> <flag> - assumes stage 1 with check h1
solve_tree() {
    runner=$1; flag=$2
    "$runner" check h1 one >/dev/null
    "$runner" stage >/dev/null
    "$runner" unlock "$flag" >/dev/null
    "$runner" finish 'FLAG{t56-final}' >/dev/null
}

ROOT1=$WORK/incident
ROOT2=$WORK/terminal
ROOT3=$WORK/old-format
make_tree "$ROOT1" 'MODE incident' 101
make_tree "$ROOT2" 'MODE terminal' 202
make_tree "$ROOT3" '' 303
FLAG1="FLAG$(printf '{%s}' "$(hexof 'frag-one' | cut -c1-12)")"

run1() { SRE_STATE_ROOT="$ROOT1" "$BIN" "$@"; }
run2() { SRE_STATE_ROOT="$ROOT2" "$BIN" "$@"; }
run3() { SRE_STATE_ROOT="$ROOT3" "$BIN" "$@"; }

printf '\n== mode parsing and hint counter ==\n'
expect_rc "incident: status parses MODE line" 0 run1 status
expect_contains "incident: status reports mode" "Mode:  incident"
expect_contains "incident: status starts at zero hints" "Hints: 0 page(s) shown"

expect_rc "first hint page" 0 run1 hint
if [ "$(tr -d '[:space:]' <"$ROOT1/state/hints" 2>/dev/null)" = "1" ]; then
    ok "state/hints incremented to 1"
else
    bad "state/hints is not 1 after first hint"
fi

expect_rc "second hint page" 0 run1 hint 2
if [ "$(tr -d '[:space:]' <"$ROOT1/state/hints" 2>/dev/null)" = "2" ]; then
    ok "state/hints incremented to 2"
else
    bad "state/hints is not 2 after second hint"
fi

expect_rc "invalid hint request is rejected" 1 run1 hint junk
if [ "$(tr -d '[:space:]' <"$ROOT1/state/hints" 2>/dev/null)" = "2" ]; then
    ok "invalid hint request does not increment the counter"
else
    bad "rejected hint request changed the counter"
fi

printf '\n== records carry mode and hints_used ==\n'
solve_tree run1 "$FLAG1"
if python3 - "$ROOT1/state/records.jsonl" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    rows = [json.loads(line) for line in fh if line.strip()]
assert len(rows) == 1, rows
r = rows[0]
assert r["mode"] == "incident", r
assert r["hints_used"] == 2, r
assert r["seed"] == 101 and r["stages"] == ["recon"], r
assert isinstance(r["epoch"], int) and isinstance(r["elapsed_seconds"], int), r
PY
then
    ok "incident record has mode, hints_used and all legacy fields"
else
    bad "incident record content is wrong"
fi

solve_tree run2 "$FLAG1"
if python3 - "$ROOT2/state/records.jsonl" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    rows = [json.loads(line) for line in fh if line.strip()]
assert len(rows) == 1, rows
r = rows[0]
assert r["mode"] == "terminal" and r["hints_used"] == 0, r
assert r["seed"] == 202 and r["stages"] == ["recon"], r
PY
then
    ok "terminal record has mode and zero hints"
else
    bad "terminal record content is wrong"
fi

printf '\n== old-format manifest and record ==\n'
expect_rc "manifest without MODE is parseable" 0 run3 status
expect_contains "old manifest defaults to terminal" "Mode:  terminal"
solve_tree run3 "$FLAG1"
if python3 - "$ROOT3/state/records.jsonl" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    rows = [json.loads(line) for line in fh if line.strip()]
assert len(rows) == 1, rows
r = rows[0]
assert r["mode"] == "terminal" and r["hints_used"] == 0, r
assert r["seed"] == 303, r
PY
then
    ok "old-format run records default mode and zero hints"
else
    bad "old-format record content is wrong"
fi

printf '\n== reset clears the counter ==\n'
expect_rc "reset after completion" 0 run1 reset
if [ ! -e "$ROOT1/state/hints" ]; then
    ok "state/hints cleared by verify reset"
else
    bad "state/hints survived verify reset"
fi
if [ "$(wc -l <"$ROOT1/state/records.jsonl" | tr -d ' ')" = "1" ]; then
    ok "record log preserved across reset"
else
    bad "reset damaged the record log"
fi
expect_rc "hint after reset" 0 run1 hint
if [ "$(tr -d '[:space:]' <"$ROOT1/state/hints" 2>/dev/null)" = "1" ]; then
    ok "hint counter restarts at 1 after reset"
else
    bad "hint counter did not restart after reset"
fi

printf '\n== best filters ==\n'
COMBINED=$WORK/records.jsonl
cat "$ROOT1/state/records.jsonl" "$ROOT2/state/records.jsonl" \
    "$ROOT3/state/records.jsonl" >"$COMBINED"
python3 - "$COMBINED" <<'PY'
import json, sys
with open(sys.argv[1], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"epoch": 1700000000, "elapsed_seconds": 42,
                         "seed": 777, "stages": ["recon"]}) + "\n")
PY

ALL_OUT=$WORK/all.txt
TERM_OUT=$WORK/terminal.txt
INC_OUT=$WORK/incident.txt
python3 "$REPO_DIR/tools/best.py" "$COMBINED" >"$ALL_OUT"
python3 "$REPO_DIR/tools/best.py" "$COMBINED" terminal >"$TERM_OUT"
python3 "$REPO_DIR/tools/best.py" "$COMBINED" incident >"$INC_OUT"

expect_contains "best header has a mode column" "mode" "$(cat "$ALL_OUT")"
expect_contains "best header has a hints column" "hints" "$(cat "$ALL_OUT")"
expect_contains "unfiltered best shows incident" "incident" "$(cat "$ALL_OUT")"
expect_contains "unfiltered best shows terminal" "terminal" "$(cat "$ALL_OUT")"
expect_contains "unfiltered best shows an old record as ?" "?" "$(cat "$ALL_OUT")"
expect_not_contains "terminal filter hides incident" "incident" "$(cat "$TERM_OUT")"
expect_contains "terminal filter keeps terminal" "terminal" "$(cat "$TERM_OUT")"
expect_not_contains "terminal filter hides old records" "?" "$(cat "$TERM_OUT")"
expect_not_contains "incident filter hides terminal" "terminal" "$(cat "$INC_OUT")"
expect_contains "incident filter keeps incident" "incident" "$(cat "$INC_OUT")"
if tail -n 1 "$ALL_OUT" | grep -q '42s'; then
    ok "best still sorts by elapsed time (42s last row)"
else
    bad "best no longer sorts by elapsed time: $(tail -n 1 "$ALL_OUT")"
fi
expect_rc "best rejects an unknown mode" 1 \
    python3 "$REPO_DIR/tools/best.py" "$COMBINED" bogus

printf '\n== make best MODE wiring ==\n'
HOST_STATE=$WORK/host-state
mkdir -p "$HOST_STATE"
cp "$COMBINED" "$HOST_STATE/records.jsonl"
MAKE_ALL=$(make -C "$REPO_DIR" --no-print-directory -s best STATE="$HOST_STATE")
MAKE_TERM=$(make -C "$REPO_DIR" --no-print-directory -s best STATE="$HOST_STATE" MODE=terminal)
MAKE_INC=$(make -C "$REPO_DIR" --no-print-directory -s best STATE="$HOST_STATE" MODE=incident)
expect_contains "make best (no MODE) shows all modes" "incident" "$MAKE_ALL"
expect_contains "make best (no MODE) keeps terminal too" "terminal" "$MAKE_ALL"
expect_not_contains "make best MODE=terminal hides incident" "incident" "$MAKE_TERM"
expect_contains "make best MODE=terminal keeps terminal" "terminal" "$MAKE_TERM"
expect_not_contains "make best MODE=incident hides terminal" "terminal" "$MAKE_INC"
expect_contains "make best MODE=incident keeps incident" "incident" "$MAKE_INC"

printf '\n== summary ==\n'
printf 'passed: %d   failed: %d\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
printf 'OK\n'
