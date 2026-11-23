#!/bin/sh
# tests/smoke.sh - end-to-end smoke test for the SRE trainer verifier.
#
# Builds src/verify.c statically, fabricates a throwaway challenge tree in a
# temp dir (using SRE_STATE_ROOT so no root access is needed), and exercises
# every subcommand, including the failure paths.
#
# Usage:  sh tests/smoke.sh [build-output-path]
# Exits 0 when every assertion passes.

set -u

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BIN=${1:-/tmp/verify}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/sre-smoke.XXXXXX")
ROOT=$WORK/sre-trainer
STATE=$ROOT/state
LOG=$WORK/build.log

pass=0
fail=0
OUT=""

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT INT TERM

ok()   { pass=$((pass + 1)); printf 'PASS  %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf 'FAIL  %s\n' "$1"; }
note() { printf '      %s\n' "$1"; }

# expect_rc <desc> <expected-rc> <cmd...>
expect_rc() {
    desc=$1
    exp=$2
    shift 2
    OUT=$("$@" 2>&1)
    rc=$?
    if [ "$rc" -eq "$exp" ]; then
        ok "$desc (rc=$rc)"
    else
        bad "$desc (expected rc=$exp, got $rc)"
        printf '%s\n' "$OUT" | sed 's/^/        | /'
    fi
}

# expect_contains <desc> <needle>
expect_contains() {
    if printf '%s\n' "$OUT" | grep -F -- "$2" >/dev/null 2>&1; then
        ok "$1"
    else
        bad "$1 (missing: $2)"
        printf '%s\n' "$OUT" | sed 's/^/        | /'
    fi
}

expect_not_contains() {
    if printf '%s\n' "$OUT" | grep -F -- "$2" >/dev/null 2>&1; then
        bad "$1 (found forbidden: $2)"
        printf '%s\n' "$OUT" | sed 's/^/        | /'
    else
        ok "$1"
    fi
}

hexof() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }
filehex() { sha256sum "$1" | cut -d' ' -f1; }

# ---------------------------------------------------------------- build
printf '== build ==\n'
if [ "${SRE_SMOKE_SKIP_BUILD:-0}" = "1" ] && [ -x "$BIN" ]; then
    ok "using prebuilt binary at $BIN (SRE_SMOKE_SKIP_BUILD=1)"
elif gcc -O2 -static -Wall -Wextra -o "$BIN" \
        "$REPO_DIR/src/verify.c" "$REPO_DIR/src/sha256.c" "$REPO_DIR/src/util.c" \
        >"$LOG" 2>&1; then
    ok "static build of src/verify.c"
else
    bad "static build failed"
    sed 's/^/        | /' "$LOG"
    exit 1
fi
if [ -s "$LOG" ]; then
    note "compiler diagnostics:"
    sed 's/^/        | /' "$LOG"
fi

printf '\n== fixture ==\n'
mkdir -p "$STATE" "$ROOT/stages/01"

cat >"$ROOT/manifest" <<EOF
SEED 424242
STAGES 1
STAGE recon
EOF

printf 'Find the passphrase, then read the root-only secret file.\n' \
    >"$ROOT/stages/01/instruction"
printf 'Read the brief carefully.\nCheck the logs.\n' >"$ROOT/stages/01/hint"

SECRET=$WORK/secret.txt
printf 'rootonly-secret\n' >"$SECRET"

H1_SHA=$(hexof alpha)
F1_SHA=$(filehex "$SECRET")

cat >"$ROOT/stages/01/checks.tsv" <<EOF
id=h1
type=hash
answer=$H1_SHA
fragment=frag-one
hint=Try the obvious word

id=f1
type=file
path=$SECRET
sha256=$F1_SHA
fragment=frag-two
hint=Look at the file contents
EOF

STAGE_FLAG="FLAG$(printf '{%s}' "$(hexof 'frag-one|frag-two' | cut -c1-12)")"
FINAL_FLAG='FLAG{complete-all-the-things}'
hexof "$STAGE_FLAG" >"$ROOT/stages/01/flag.sha256"
hexof "$FINAL_FLAG" >"$ROOT/final.sha256"

note "expected stage flag: $STAGE_FLAG"
OK=0
NG=0

run() { SRE_STATE_ROOT="$ROOT" "$BIN" "$@"; }

# ---------------------------------------------------------------- status/next
printf '\n== status, hints, next ==\n'
expect_rc "status before start" 0 run status
expect_contains "status shows seed" "424242"
expect_contains "status shows stage 1/1" "Stage: 1/1 (recon)"
expect_contains "status shows no clock yet" "not started"
expect_contains "status counts 0/2 checks" "Checks: 0/2 done"

expect_rc "hint (default n=1)" 0 run hint
expect_contains "hint shows first hint" "Hint 1/4: Read the brief carefully."
expect_contains "hint advertises the next one" "3 more: verify hint 2"

expect_rc "hint 4 shows everything" 0 run hint 4
expect_contains "hint 4 reaches the last hint" "Hint 4/4: Look at the file contents"
expect_not_contains "hints do not leak the hash answer" "$H1_SHA"

expect_rc "hint rejects junk" 1 run hint zero

expect_rc "next shows the brief" 0 run next
expect_contains "next prints instruction" "Find the passphrase"
expect_contains "next lists check ids" "h1, f1"
if [ -s "$STATE/clock" ]; then ok "next started the clock"; else bad "clock missing after next"; fi
if [ "$(tr -d '[:space:]' <"$STATE/progress" 2>/dev/null)" = "1" ] || [ ! -e "$STATE/progress" ]; then
    ok "progress starts at 1"
else
    bad "progress is not 1"
fi

# ---------------------------------------------------------------- rejections
printf '\n== rejections ==\n'
expect_rc "wrong hash answer rejected" 1 run check h1 alfa
expect_contains "wrong answer says NOT OK" "NOT OK"

expect_rc "unknown check id rejected" 1 run check nosuch x
expect_contains "unknown id names the stage scope" "no check 'nosuch'"

expect_rc "path traversal in check-id rejected" 1 run check '../../etc/passwd' x
expect_contains "traversal message" "invalid check id"

expect_rc "overlong check id rejected" 1 run check "$(printf 'a%.0s' $(seq 1 80))" x

expect_rc "stage before finishing checks" 1 run stage
expect_contains "stage lists remaining ids" "Remaining check ids"

if [ ! -e "$STATE/stage_01.frag" ]; then ok "no fragment written on failures"; else bad "fragment leaked on failure"; fi

# ---------------------------------------------------------------- success path
printf '\n== happy path ==\n'
expect_rc "correct hash answer accepted" 0 run check h1 alpha
expect_contains "OK line shows the fragment" "OK  (h1): frag-one"
expect_contains "progress counts 1/2" "Progress: 1/2 checks done"

expect_rc "duplicate check refused" 1 run check h1 alpha
expect_contains "duplicate message" "already completed"

# type=file validates the *file*, not argv (the answer argument is ignored).
# Prove it by tampering with the file content the check hashes.
cp "$SECRET" "$WORK/secret.bak"
printf 'tampered\n' >"$SECRET"
expect_rc "file check rejects tampered content" 1 run check f1 placeholder
cp "$WORK/secret.bak" "$SECRET"
expect_rc "file check accepts the restored content" 0 run check f1 ignored
expect_contains "second fragment recorded" "OK  (f1): frag-two"

if [ "$(wc -l <"$STATE/stage_01.frag" | tr -d ' ')" = "2" ]; then
    ok "two fragments appended to stage_01.frag"
else
    bad "unexpected fragment count"
fi

expect_rc "stage earns the flag" 0 run stage
expect_contains "flag matches the expected value" "$STAGE_FLAG"
expect_not_contains "stage never prints a raw digest" "$H1_SHA"

# Solve order must not change the flag: reset, complete in the other order.
expect_rc "reset for order experiment" 0 run reset
expect_rc "check f1 first" 0 run check f1 "$SECRET"
expect_rc "check h1 second" 0 run check h1 alpha
expect_rc "stage after reversed order" 0 run stage
expect_contains "reversed order yields the same flag" "$STAGE_FLAG"

# ---------------------------------------------------------------- unlock/finish
printf '\n== unlock and finish ==\n'
expect_rc "wrong flag refused" 1 run unlock 'FLAG{000000000000}'
expect_contains "wrong flag message" "NOT OK"
if [ "$(tr -d '[:space:]' <"$STATE/progress")" = "1" ]; then ok "progress unchanged after bad flag"; else bad "progress advanced on bad flag"; fi

expect_rc "finish refused before unlock" 1 run finish "$FINAL_FLAG"
expect_contains "finish explains why" "not all stages are unlocked"

expect_rc "correct flag unlocks the last stage" 0 run unlock "$STAGE_FLAG"
expect_contains "unlock reports completion" "everything is unlocked"
expect_contains "unlock tells you how to finish" "verify finish"
if [ ! -e "$STATE/stage_01.frag" ] && [ ! -e "$STATE/stage_01.done" ]; then
    ok "unlocked stage's frag/done files cleared"
else
    bad "frag/done not cleared on unlock"
fi
if [ "$(tr -d '[:space:]' <"$STATE/progress")" = "2" ]; then ok "progress advanced to 2"; else bad "progress not advanced"; fi

expect_rc "status after unlock" 0 run status
expect_contains "status reports all complete" "all 1 complete"

expect_rc "wrong final flag refused" 1 run finish 'FLAG{nope}'
expect_rc "correct final flag accepted" 0 run finish "$FINAL_FLAG"
expect_contains "finish prints elapsed" "Elapsed:"
if [ -f "$STATE/records.jsonl" ] && [ "$(wc -l <"$STATE/records.jsonl" | tr -d ' ')" = "1" ]; then
    ok "one record appended"
else
    bad "records.jsonl missing or not exactly one line"
fi
RECORD=$(cat "$STATE/records.jsonl" 2>/dev/null)
case $RECORD in
    *'"seed":424242'*) ok "record carries the seed" ;;
    *) bad "record missing seed: $RECORD" ;;
esac
case $RECORD in
    *'"stages":["recon"]'*) ok "record lists stage ids" ;;
    *) bad "record missing stage ids: $RECORD" ;;
esac
case $RECORD in
    *'"elapsed_seconds":'*) ok "record carries elapsed_seconds" ;;
    *) bad "record missing elapsed_seconds" ;;
esac
case $RECORD in
    *'"epoch":'*) ok "record carries epoch" ;;
    *) bad "record missing epoch" ;;
esac
expect_not_contains "finish never echoes the flag" "$FINAL_FLAG"

expect_rc "second finish is refused" 1 run finish "$FINAL_FLAG"
expect_contains "second finish explains why" "already recorded"
if [ -f "$STATE/finished" ]; then ok "finished marker written"; else bad "finished marker missing"; fi
if [ "$(wc -l <"$STATE/records.jsonl" | tr -d ' ')" = "1" ]; then
    ok "second finish did not append a duplicate record"
else
    bad "duplicate record appended by a repeated finish"
fi

# ---------------------------------------------------------------- reset
printf '\n== reset ==\n'
expect_rc "reset after completion" 0 run reset
expect_contains "reset confirmation" "Back at stage 1"
if [ "$(tr -d '[:space:]' <"$STATE/progress")" = "1" ]; then ok "progress back to 1"; else bad "progress not reset"; fi
if [ ! -e "$STATE/clock" ]; then ok "clock cleared"; else bad "clock survived reset"; fi
if [ ! -e "$STATE/finished" ]; then ok "finished marker cleared"; else bad "finished marker survived reset"; fi
if [ -e "$STATE/records.jsonl" ] && [ "$(wc -l <"$STATE/records.jsonl" | tr -d ' ')" = "1" ]; then
    ok "records.jsonl preserved across reset"
else
    bad "reset deleted the completion log"
fi
if [ -f "$ROOT/final.sha256" ] && [ -f "$ROOT/stages/01/checks.tsv" ]; then
    ok "challenge files untouched by reset"
else
    bad "reset damaged challenge inputs"
fi

expect_rc "status after reset" 0 run status
expect_contains "status is back at stage 1" "Stage: 1/1 (recon)"

# ---------------------------------------------------------------- two stages
printf '\n== two-stage tree ==\n'
ROOT2=$WORK/sre-trainer-2
mkdir -p "$ROOT2/state" "$ROOT2/stages/01" "$ROOT2/stages/02"

cat >"$ROOT2/manifest" <<'EOF'
SEED 99
STAGES 2
STAGE recon
STAGE escalate
EOF

printf 'Alpha brief only.\n'  >"$ROOT2/stages/01/instruction"
printf 'Alpha hint one.\n'    >"$ROOT2/stages/01/hint"
printf 'Bravo brief, do not leak.\n' >"$ROOT2/stages/02/instruction"
printf 'Bravo hint one.\n'    >"$ROOT2/stages/02/hint"

cat >"$ROOT2/stages/01/checks.tsv" <<EOF
id=a1
type=hash
answer=$(hexof one)
fragment=frag-zero
hint=Alpha check hint

EOF
cat >"$ROOT2/stages/02/checks.tsv" <<EOF
id=b1
type=hash
answer=$(hexof two)
fragment=frag-three
hint=Bravo check hint

EOF

FLAG1="FLAG$(printf '{%s}' "$(hexof 'frag-zero' | cut -c1-12)")"
FLAG2="FLAG$(printf '{%s}' "$(hexof 'frag-three' | cut -c1-12)")"
hexof "$FLAG1" >"$ROOT2/stages/01/flag.sha256"
hexof "$FLAG2" >"$ROOT2/stages/02/flag.sha256"
hexof 'FLAG{two-stages-final}' >"$ROOT2/final.sha256"

run2() { SRE_STATE_ROOT="$ROOT2" "$BIN" "$@"; }

expect_rc "multi: status at stage 1" 0 run2 status
expect_contains "multi: shows stage 1/2" "Stage: 1/2 (recon)"
expect_not_contains "multi: does not reveal the next stage name" "escalate"

expect_rc "multi: next at stage 1" 0 run2 next
expect_contains "multi: shows alpha brief" "Alpha brief only."
expect_not_contains "multi: hides the alpha->bravo brief" "Bravo brief"

expect_rc "multi: hint at stage 1" 0 run2 hint 9
expect_contains "multi: alpha hints only" "Alpha check hint"
expect_not_contains "multi: no bravo hints yet" "Bravo hint one."

expect_rc "multi: solve a1" 0 run2 check a1 one
expect_rc "multi: stage flag 1" 0 run2 stage
expect_contains "multi: flag 1 correct" "$FLAG1"

expect_rc "multi: unlock stage 1 advances" 0 run2 unlock "$FLAG1"
expect_contains "multi: unlock names the stage" "Stage 1 (recon) unlocked."
expect_contains "multi: prints the next brief" "Bravo brief, do not leak."
expect_contains "multi: next brief is stage 2/2" "Stage 2/2: escalate"
if [ "$(tr -d '[:space:]' <"$ROOT2/state/progress")" = "2" ]; then ok "multi: progress is 2"; else bad "multi: progress not 2"; fi

expect_rc "multi: finish blocked at stage 2" 1 run2 finish 'FLAG{two-stages-final}'
expect_contains "multi: finish says stage 2/2" "stage 2/2"

expect_rc "multi: solve b1" 0 run2 check b1 two
expect_rc "multi: stage flag 2" 0 run2 stage
expect_contains "multi: flag 2 correct" "$FLAG2"
expect_rc "multi: unlock the last stage" 0 run2 unlock "$FLAG2"
expect_contains "multi: last-stage completion" "everything is unlocked"
expect_rc "multi: final flag accepted" 0 run2 finish 'FLAG{two-stages-final}'
RECORD2=$(cat "$ROOT2/state/records.jsonl" 2>/dev/null)
case $RECORD2 in
    *'"stages":["recon","escalate"]'*) ok "multi: record lists both stage ids in play order" ;;
    *) bad "multi: record stage list wrong: $RECORD2" ;;
esac
case $RECORD2 in
    *'"seed":99'*) ok "multi: record carries seed 99" ;;
    *) bad "multi: seed missing: $RECORD2" ;;
esac

# ---------------------------------------------------------------- misc
printf '\n== misc ==\n'
expect_rc "no arguments is a usage error" 2 run
expect_rc "--help works" 0 run --help
expect_contains "help lists finish" "finish <final-flag>"
expect_rc "unknown command rejected" 2 run frobnicate

if [ -s "$BIN" ]; then
    if file "$BIN" 2>/dev/null | grep -q 'statically linked'; then
        ok "binary is statically linked"
    else
        note "could not confirm static linking with file(1)"
    fi
fi

printf '\n== summary ==\n'
printf 'passed: %d   failed: %d\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
printf 'OK\n'
