#!/bin/sh
# demo/render.sh - record the sample attempt (docs/demo/sample-run.{mp4,gif}).
#
# The recording is driven by VHS against the REAL container: render.sh builds
# the drills for a fixed seed, reads the generated answers, substitutes them
# into demo/sample.tape, and lets VHS type the commands into a real shell and
# a real sshd session.  Nothing on screen is scripted or faked.
#
# Everything happens on a throwaway demo instance (.demo-instance/,
# .demo-state/, container sre-trainer-demo, port 2299) so the caller's own
# .instance/, state/ and container are never touched.  The demo instance is
# removed again, even if the recording fails.
#
# Usage: demo/render.sh [both|mp4|gif]        (make demo | demo-mp4 | demo-gif)
# Env:   SEED (fixed seed, default 8), SET (stages, default 8), PORT (2299),
#        ENGINE (podman|docker, auto-detected)

set -eu

MODE=${1:-both}
case $MODE in
  both|mp4|gif) ;;
  *) echo "usage: $0 [both|mp4|gif]" >&2; exit 2 ;;
esac

REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO"

# The demo instance is deliberately fixed (no overrides): the cleanup trap
# removes exactly these paths and this container, and nothing else.
INSTANCE=.demo-instance
STATE=.demo-state
CONTAINER=sre-trainer-demo
PORT=${PORT:-2299}
SEED=${SEED:-8}
SET=${SET:-8}
OUTDIR=$REPO/docs/demo
OUT=$OUTDIR/sample-run
DEMOENV="INSTANCE=$INSTANCE STATE=$STATE CONTAINER=$CONTAINER PORT=$PORT"
ENGINE=${ENGINE:-$(command -v podman >/dev/null 2>&1 && echo podman || echo docker)}

for tool in vhs python3 make ssh "$ENGINE"; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "error: '$tool' is required to render the demo" >&2
    echo "       (vhs: https://github.com/charmbracelet/vhs)" >&2
    exit 1
  }
done

# `make demo` exports MAKELEVEL/MAKEFLAGS; without this the recorded shell would
# inherit them and every `make` it runs would print "make[1]: Entering ...".
unset MAKELEVEL MAKEFLAGS MFLAGS 2>/dev/null || true

TMPROOT=${TMPDIR:-/tmp/opencode}
mkdir -p "$TMPROOT"
WORK=$(mktemp -d "$TMPROOT/sre-demo.XXXXXX")
mkdir -p "$WORK/gen"

cleanup() {
  rc=$?
  trap - EXIT INT TERM
  # Always remove the demo container, its credentials and its records.
  ( cd "$REPO" && ENGINE=$ENGINE make --no-print-directory down \
      INSTANCE=$INSTANCE STATE=$STATE CONTAINER=$CONTAINER PORT=$PORT ) \
      >/dev/null 2>&1 || "$ENGINE" rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$REPO/$INSTANCE" "$REPO/$STATE" "$WORK"
  exit $rc
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# -- 1. regenerate the same challenge set on the host and read its answers --
printf '==> seed %s, set %s: generating the answer key on the host\n' "$SEED" "$SET"
python3 generator/generate.py --seed "$SEED" --set "$SET" \
  --out "$WORK/gen" --dump-answers "$WORK/gen/answers.json" >/dev/null

python3 - "$WORK/gen/answers.json" "$WORK/gen/stages/01/instruction" > "$WORK/vars" <<'PY'
import json
import re
import sys

dump = json.load(open(sys.argv[1], encoding="utf-8"))
instruction = open(sys.argv[2], encoding="utf-8").read()

stage = dump["order"][0]
if stage != "deploy-error":
    sys.exit("seed %s starts with %r; the demo tape drives deploy-error "
             "(pick a seed whose stage 1 is deploy-error)" % (dump["seed"], stage))

checks = dump["stages"][stage]["checks"]
if len(checks) != 3:
    sys.exit("stage 1 has %d checks, the tape submits 3" % len(checks))

match = re.search(r"/var/log/sre/[^ \n]+\.log", instruction)
if not match:
    sys.exit("could not find the stage-1 log path in the instruction")
log = match.group(0)


def safe(value):
    """Only substitute generator tokens; never shell or tape metacharacters."""
    if not re.fullmatch(r"[A-Za-z0-9._/{}-]+", value):
        sys.exit("refusing to substitute %r into the tape" % value)
    return value


print("STAGE1=%s" % safe(stage))
print("LOG=%s" % safe(log))
for i, check in enumerate(checks, 1):
    print("ID%d=%s" % (i, safe(check["id"])))
    print("A%d=%s" % (i, safe(check["answer"])))
print("FLAG1=%s" % safe(dump["stages"][stage]["flag"]))
PY

# shellcheck disable=SC1091
. "$WORK/vars"
printf '==> stage 1 is %s at %s\n' "$STAGE1" "$LOG"

# -- 2. warm the image cache outside the recording, so the video shows a real
#       `make up` that is not dominated by a cold apk/gcc build ---------------
printf '==> warming the image (not recorded)\n'
make --no-print-directory up SEED="$SEED" SET="$SET" \
  INSTANCE=$INSTANCE STATE=$STATE CONTAINER=$CONTAINER PORT=$PORT >/dev/null
make --no-print-directory down \
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
  -e "s|__LOG__|$LOG|g" \
  -e "s|__ID1__|$ID1|g" -e "s|__A1__|$A1|g" \
  -e "s|__ID2__|$ID2|g" -e "s|__A2__|$A2|g" \
  -e "s|__ID3__|$ID3|g" -e "s|__A3__|$A3|g" \
  -e "s|__FLAG1__|$FLAG1|g" \
  -e "s|__OUT__|$OUTDIR|g" \
  demo/sample.tape > "$WORK/sample.tape"

case $MODE in
  mp4) grep -v '\.gif"' "$WORK/sample.tape" > "$WORK/one.tape" ;;
  gif) grep -v '\.mp4"' "$WORK/sample.tape" > "$WORK/one.tape" ;;
  both) cp "$WORK/sample.tape" "$WORK/one.tape" ;;
esac
mv "$WORK/one.tape" "$WORK/sample.tape"

mkdir -p "$OUTDIR"
case $MODE in
  both) rm -f "$OUT.mp4" "$OUT.gif" ;;
  mp4) rm -f "$OUT.mp4" ;;
  gif) rm -f "$OUT.gif" ;;
esac

# -- 4. record: VHS drives the real container, in its own scratch directory --
printf '==> recording (this builds the demo container for real)\n'
if ! ( cd "$WORK" && vhs sample.tape >vhs.log 2>&1 ); then
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

# A 65-second 1280x800 recording makes a heavy GIF.  gifsicle is preferred
# (lossy, keeps every frame and the text crisp); where it is not installed,
# re-time the GIF to 12 fps with a 128-colour palette so it still renders
# inline on GitHub.  Never replace the original unless the result is smaller.
if [ -f "$OUT.gif" ]; then
  before=$(wc -c < "$OUT.gif")
  if [ "$before" -gt 6000000 ]; then
    if command -v gifsicle >/dev/null 2>&1; then
      printf '==> shrinking %s (%s bytes) with gifsicle\n' "$OUT.gif" "$before"
      gifsicle -O3 --lossy=80 --colors 200 "$OUT.gif" -o "$WORK/small.gif" 2>/dev/null || true
    elif command -v ffmpeg >/dev/null 2>&1; then
      printf '==> shrinking %s (%s bytes) with ffmpeg (12 fps, 128 colours)\n' \
        "$OUT.gif" "$before"
      ffmpeg -v error -y -i "$OUT.gif" -loop 0 \
        -vf 'fps=12,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle' \
        "$WORK/small.gif" 2>/dev/null || true
    fi
    if [ -s "$WORK/small.gif" ] && [ "$(wc -c < "$WORK/small.gif")" -lt "$before" ]; then
      mv "$WORK/small.gif" "$OUT.gif"
    else
      rm -f "$WORK/small.gif"
    fi
  fi
fi

printf '\n==> demo recordings\n'
for f in "$OUT.mp4" "$OUT.gif"; do
  [ -f "$f" ] || continue
  size=$(wc -c < "$f")
  info=$(ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height,duration -of csv=p=0 "$f" 2>/dev/null || true)
  printf '  %-40s %8s bytes  %s\n' "${f#$REPO/}" "$size" "$info"
done
