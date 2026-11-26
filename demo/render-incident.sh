#!/bin/sh
# demo/render-incident.sh - record the Incident mode attempt
# (docs/demo/incident-run.{mp4,gif}).
#
# Like demo/render.sh, but for MODE=incident PICK=k8s-crashloop: it generates
# the answer key for a fixed seed on the host, substitutes the real answers
# into demo/incident.tape, and lets VHS type the commands into a real shell and
# a real sshd session against a real throwaway container.  Nothing on screen is
# faked.  Both outputs come from that single tape run.
#
# Everything uses its own throwaway instance (.demo-incident-instance/,
# .demo-incident-state/, container sre-trainer-demo-incident, port 2301) so the
# caller's .instance/, state/, real container, and the Terminal demo's
# .demo-instance/ are never touched.  It is removed even if the recording
# fails.
#
# Usage: demo/render-incident.sh [both|mp4|gif]     (make demo-incident)
# Env:   SEED (fixed seed, default 8), PORT (2301), ENGINE (podman|docker,
#        auto-detected).  The template and instance names are fixed on purpose.

set -eu

MODE=${1:-both}
case $MODE in
  both|mp4|gif) ;;
  *) echo "usage: $0 [both|mp4|gif]" >&2; exit 2 ;;
esac

REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO"

INSTANCE=.demo-incident-instance
STATE=.demo-incident-state
CONTAINER=sre-trainer-demo-incident
PORT=${PORT:-2301}
SEED=${SEED:-8}
SET=1
PICK=k8s-crashloop
OUTDIR=$REPO/docs/demo
OUT=$OUTDIR/incident-run
DEMOENV="INSTANCE=$INSTANCE STATE=$STATE CONTAINER=$CONTAINER PORT=$PORT"
ENGINE=${ENGINE:-$(command -v podman >/dev/null 2>&1 && echo podman || echo docker)}

for tool in vhs python3 make ssh "$ENGINE"; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "error: '$tool' is required to render the incident demo" >&2
    echo "       (vhs: https://github.com/charmbracelet/vhs)" >&2
    exit 1
  }
done

# `make demo-incident` exports MAKELEVEL/MAKEFLAGS; without this the recorded
# shell would inherit them and every `make` would print "make[1]: Entering ...".
unset MAKELEVEL MAKEFLAGS MFLAGS 2>/dev/null || true

TMPROOT=${TMPDIR:-/tmp/opencode}
mkdir -p "$TMPROOT"
WORK=$(mktemp -d "$TMPROOT/sre-incident-demo.XXXXXX")
mkdir -p "$WORK/gen"

cleanup() {
  rc=$?
  trap - EXIT INT TERM
  ( cd "$REPO" && ENGINE=$ENGINE make --no-print-directory down \
      MODE=incident INSTANCE=$INSTANCE STATE=$STATE \
      CONTAINER=$CONTAINER PORT=$PORT ) >/dev/null 2>&1 \
      || "$ENGINE" rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$REPO/$INSTANCE" "$REPO/$STATE" "$WORK"
  exit $rc
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# -- 1. regenerate the same single incident on the host and read its answers --
printf '==> seed %s, PICK=%s: generating the incident answer key on the host\n' \
  "$SEED" "$PICK"
python3 generator/generate.py --seed "$SEED" --set "$SET" --out "$WORK/gen" \
  --dump-answers "$WORK/gen/answers.json" --mode incident --pick "$PICK" >/dev/null

python3 - "$WORK/gen/answers.json" "$WORK/gen/manifest" > "$WORK/vars" <<'PY'
import json
import re
import sys

dump = json.load(open(sys.argv[1], encoding="utf-8"))
manifest = open(sys.argv[2], encoding="utf-8").read().splitlines()

if not manifest or manifest[0] != "MODE incident":
    sys.exit("expected an incident manifest, got %r" % (manifest[:1],))
if dump["order"] != ["k8s-crashloop"]:
    sys.exit("expected only k8s-crashloop, got %r" % (dump["order"],))

stage = dump["order"][0]
checks = dump["stages"][stage]["checks"]
if len(checks) != 3:
    sys.exit("k8s-crashloop has %d checks, the tape drives 3" % len(checks))
if [c["type"] for c in checks] != ["hash", "hash", "file"]:
    sys.exit("unexpected k8s-crashloop check types")

a1 = checks[0]["answer"]
pod, sep, reason = a1.partition(":")
if not sep or not pod or not reason:
    sys.exit("check 1 answer is not pod:reason: %r" % a1)
missing = checks[1]["answer"]
actual = checks[2]["fragment"]
path = checks[2]["path"]
if not missing or missing == actual:
    sys.exit("file repair keys are inconsistent: %r -> %r" % (missing, actual))


def safe(value):
    """Only substitute generator tokens; never shell or tape metacharacters."""
    if not re.fullmatch(r"[A-Za-z0-9._/{}:-]+", value):
        sys.exit("refusing to substitute %r into the tape" % value)
    return value


print("POD=%s" % safe(pod))
print("A1=%s" % safe(a1))
print("A2=%s" % safe(checks[1]["answer"]))
print("MISSING=%s" % safe(missing))
print("ACTUAL=%s" % safe(actual))
print("MANIFEST=%s" % safe(path))
for i, check in enumerate(checks, 1):
    print("ID%d=%s" % (i, safe(check["id"])))
print("FLAG1=%s" % safe(dump["stages"][stage]["flag"]))
PY

# shellcheck disable=SC1091
. "$WORK/vars"
printf '==> incident is %s (pod %s, %s -> %s)\n' \
  "$PICK" "$POD" "$MISSING" "$ACTUAL"

# -- 2. warm the image cache outside the recording, so the recorded `make up`
#       is a cached build rather than a cold apk/gcc build --------------------
printf '==> warming the image (not recorded)\n'
make --no-print-directory up MODE=incident PICK="$PICK" SET="$SET" SEED="$SEED" \
  INSTANCE=$INSTANCE STATE=$STATE CONTAINER=$CONTAINER PORT=$PORT >/dev/null
make --no-print-directory down MODE=incident \
  INSTANCE=$INSTANCE STATE=$STATE CONTAINER=$CONTAINER PORT=$PORT >/dev/null

PASSWORD=$(sed -n 's/^SSH_PASSWORD=//p' "$INSTANCE/creds")
if [ -z "$PASSWORD" ]; then
  echo 'error: no SSH_PASSWORD in '"$INSTANCE/creds" >&2
  exit 1
fi

# -- 3. fill the tape template in -------------------------------------------
cp demo/style.tape "$WORK/style.tape"
sed \
  -e "s|__REPO__|$REPO|g" \
  -e "s|__ENGINE__|$ENGINE|g" \
  -e "s|__SEED__|$SEED|g" \
  -e "s|__DEMOENV__|$DEMOENV|g" \
  -e "s|__PASSWORD__|$PASSWORD|g" \
  -e "s|__POD__|$POD|g" \
  -e "s|__A1__|$A1|g" -e "s|__A2__|$A2|g" \
  -e "s|__MISSING__|$MISSING|g" \
  -e "s|__ACTUAL__|$ACTUAL|g" \
  -e "s|__MANIFEST__|$MANIFEST|g" \
  -e "s|__ID1__|$ID1|g" -e "s|__ID2__|$ID2|g" -e "s|__ID3__|$ID3|g" \
  -e "s|__FLAG1__|$FLAG1|g" \
  -e "s|__OUT__|$OUTDIR|g" \
  demo/incident.tape > "$WORK/incident.tape"

# Fail loudly rather than record a tape with an unanswered placeholder.
if grep -n '__[A-Z0-9_]*__' "$WORK/incident.tape"; then
  echo 'error: unsubstituted placeholder in the generated tape' >&2
  exit 1
fi

case $MODE in
  mp4) grep -v '\.gif"' "$WORK/incident.tape" > "$WORK/one.tape" ;;
  gif) grep -v '\.mp4"' "$WORK/incident.tape" > "$WORK/one.tape" ;;
  both) cp "$WORK/incident.tape" "$WORK/one.tape" ;;
esac
mv "$WORK/one.tape" "$WORK/incident.tape"

mkdir -p "$OUTDIR"
case $MODE in
  both) rm -f "$OUT.mp4" "$OUT.gif" ;;
  mp4) rm -f "$OUT.mp4" ;;
  gif) rm -f "$OUT.gif" ;;
esac

# -- 4. record: VHS drives the real container, in its own scratch directory --
printf '==> recording (this builds the incident container for real)\n'
if ! ( cd "$WORK" && vhs incident.tape >vhs.log 2>&1 ); then
  echo 'error: vhs failed; last lines:' >&2
  tail -n 30 "$WORK/vhs.log" >&2
  exit 1
fi
tail -n 3 "$WORK/vhs.log"

# -- 5. deliverables --------------------------------------------------------
for f in "$OUT.mp4" "$OUT.gif"; do
  if [ -f "$f" ] && [ ! -s "$f" ]; then rm -f "$f"; fi
done
case $MODE in
  both) need="$OUT.mp4 $OUT.gif" ;;
  mp4) need=$OUT.mp4 ;;
  gif) need=$OUT.gif ;;
esac
for f in $need; do
  [ -s "$f" ] || { echo "error: $f was not produced" >&2; exit 1; }
done

# gifsicle is preferred (lossy, crisp text); where it is not installed, re-time
# the GIF to 12 fps with a 128-colour palette.  Never replace the original
# unless the result is smaller.
if [ -f "$OUT.gif" ]; then
  before=$(wc -c < "$OUT.gif")
  if [ "$before" -gt 6000000 ]; then
    if command -v gifsicle >/dev/null 2>&1; then
      printf '==> shrinking %s (%s bytes) with gifsicle\n' "$OUT.gif" "$before"
      gifsicle -O3 --lossy=80 --colors 200 "$OUT.gif" -o "$WORK/small.gif" 2>/dev/null || true
    elif command -v ffmpeg >/dev/null 2>&1; then
      printf '==> shrinking %s (%s bytes) with ffmpeg (10 fps, 96 colours)\n' \
        "$OUT.gif" "$before"
      ffmpeg -v error -y -i "$OUT.gif" -loop 0 \
        -vf 'fps=10,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle' \
        "$WORK/small.gif" 2>/dev/null || true
    fi
    if [ -s "$WORK/small.gif" ] && [ "$(wc -c < "$WORK/small.gif")" -lt "$before" ]; then
      mv "$WORK/small.gif" "$OUT.gif"
    else
      rm -f "$WORK/small.gif"
    fi
  fi
fi

printf '\n==> incident demo recordings\n'
for f in "$OUT.mp4" "$OUT.gif"; do
  [ -f "$f" ] || continue
  size=$(wc -c < "$f")
  info=$(ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height,duration -of csv=p=0 "$f" 2>/dev/null || true)
  printf '  %-42s %8s bytes  %s\n' "${f#$REPO/}" "$size" "$info"
done
