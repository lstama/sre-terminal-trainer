#!/bin/sh
# tests/troubleshoot_smoke.sh - smoke test for the troubleshoot pilot.
#
# Part 1 (always runs): compile src/sremock.c statically, install it in a temp
# PATH under the command names it mocks, point SREMOCK_ROOT at a temp fixture
# directory, and assert the exact fixture bytes for systemctl, journalctl,
# kubectl and docker - plus the rejection paths and byte-for-byte passthrough.
#
# Part 2 (runs when the generator has --profile support): generate the two
# pilot stages with `--profile troubleshoot --pick svc-crashloop,k8s-crashloop`
# and assert the fixture contract, the manifest, the checks.tsv shape, that
# each stage's flag recomputes from its fragments, and that no file check ships
# already solved. If the generator has no troubleshoot profile yet, part 2 is
# reported as pending (a SKIP, not a failure).
#
# Nothing here touches the network, real credentials, systemd or a cluster;
# every artefact lives under one mktemp directory that is removed on exit.
#
# Usage:
#   sh tests/troubleshoot_smoke.sh [sremock-build-output]
#   SRE_SMOKE_SKIP_BUILD=1 sh tests/troubleshoot_smoke.sh /path/to/sremock
#
# Environment:
#   SRE_SMOKE_SEED        generator seed for part 2 (default 20261124)
#   SRE_SMOKE_SKIP_BUILD  reuse an existing binary at the given path
#
# Exits 0 when every assertion passes.

set -u

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/sre-troubleshoot-smoke.XXXXXX")
BIN=${1:-$WORK/sremock}
FIX=$WORK/fixtures
BIN_DIR=$WORK/bin
BUILD_LOG=$WORK/build.log
SEED=${SRE_SMOKE_SEED:-20261124}

pass=0
fail=0
skip=0

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT INT TERM

ok()   { pass=$((pass + 1)); printf 'PASS  %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf 'FAIL  %s\n' "$1"; }
note() { printf '      %s\n' "$1"; }
skip_() { skip=$((skip + 1)); printf 'SKIP  %s\n' "$1"; }

# The 19 command names the runtime symlinks to sremock (T16 contract order).
MOCK_COMMANDS='systemctl journalctl ps ss curl df free top du nginx getenforce ausearch dig nslookup ping nc docker podman kubectl'

# ---------------------------------------------------------------- build
printf '== build ==\n'
if [ "${SRE_SMOKE_SKIP_BUILD:-0}" = "1" ] && [ -x "$BIN" ]; then
    ok "using prebuilt binary at $BIN (SRE_SMOKE_SKIP_BUILD=1)"
elif gcc -O2 -static -Wall -Wextra -o "$BIN" "$REPO_DIR/src/sremock.c" >"$BUILD_LOG" 2>&1; then
    ok "static build of src/sremock.c"
else
    bad "static build failed"
    sed 's/^/        | /' "$BUILD_LOG"
    exit 1
fi
if [ -s "$BUILD_LOG" ]; then
    note "compiler diagnostics:"
    sed 's/^/        | /' "$BUILD_LOG"
fi

# ---------------------------------------------------------------- harness
mkdir -p "$FIX" "$BIN_DIR"
for command in $MOCK_COMMANDS; do
    ln -s "$BIN" "$BIN_DIR/$command"
done
PATH=$BIN_DIR:$PATH
SREMOCK_ROOT=$FIX
export PATH SREMOCK_ROOT

# expect_fixture <desc> <fixture-name> <cmd...>
# The command must exit 0 and its stdout must be byte-identical to the fixture.
expect_fixture() {
    ef_desc=$1
    ef_name=$2
    shift 2
    "$@" >"$WORK/out" 2>"$WORK/err"
    ef_rc=$?
    if [ "$ef_rc" -eq 0 ] && cmp -s "$WORK/out" "$FIX/$ef_name"; then
        ok "$ef_desc"
    else
        bad "$ef_desc (rc=$ef_rc, expected the exact bytes of $ef_name)"
        note "stdout:"; sed 's/^/        | /' "$WORK/out"
        note "stderr:"; sed 's/^/        | /' "$WORK/err"
    fi
}

# expect_reject <desc> <stderr-needle> <cmd...>
# The command must exit nonzero, print the needle on stderr and write no stdout.
expect_reject() {
    er_desc=$1
    er_needle=$2
    shift 2
    "$@" >"$WORK/out" 2>"$WORK/err"
    er_rc=$?
    if [ "$er_rc" -ne 0 ] && grep -F -- "$er_needle" "$WORK/err" >/dev/null 2>&1 \
            && [ ! -s "$WORK/out" ]; then
        ok "$er_desc (rc=$er_rc)"
    else
        bad "$er_desc (rc=$er_rc, need stderr '$er_needle' and empty stdout)"
        note "stdout:"; sed 's/^/        | /' "$WORK/out"
        note "stderr:"; sed 's/^/        | /' "$WORK/err"
    fi
}

# ---------------------------------------------------------------- fixtures
printf '\n== fixtures ==\n'

cat >"$FIX/systemctl_status.txt" <<'EOF'
* demo.service - demo service
     Loaded: loaded (/etc/systemd/system/demo.service; enabled)
     Active: activating (auto-restart) (Result: exit-code)
   Main PID: 4242 (code=exited, status=1/FAILURE)
EOF

cat >"$FIX/journalctl_unit.txt" <<'EOF'
-- unit demo.service --
demo[4242]: FATAL: startup aborted with error E-demo
systemd[1]: demo.service: Failed with result 'exit-code'.
EOF

cat >"$FIX/journalctl_xe.txt" <<'EOF'
-- Boot fixture --
demo[4242]: FATAL: address already in use
systemd[1]: demo.service: Failed with result 'exit-code'.
EOF

cat >"$FIX/kubectl_get_pods.txt" <<'EOF'
NAME                          READY   STATUS             RESTARTS   AGE
demo-aaaa-bbbb                0/1     CrashLoopBackOff   7          42m
EOF

cat >"$FIX/kubectl_describe_pod.txt" <<'EOF'
Name:     demo-aaaa-bbbb
Namespace: demo
State:    Waiting (CrashLoopBackOff)
EOF

cat >"$FIX/kubectl_logs.txt" <<'EOF'
api: FATAL config lookup failed: key 'DEMO_KEY' is missing
EOF

cat >"$FIX/kubectl_get_events.txt" <<'EOF'
LAST SEEN   TYPE      REASON    OBJECT            MESSAGE
2m          Warning   BackOff   pod/demo-aaaa     Back-off restarting failed container
EOF

cat >"$FIX/kubectl_get_configmap.txt" <<'EOF'
Name:  demo-config
Data
====
DEMO_ACTUAL_KEY: demo-value
EOF

cat >"$FIX/kubectl_get_deploy.txt" <<'EOF'
NAME          READY   UP-TO-DATE   AVAILABLE   AGE
demo          0/1     1            0           42m
EOF

cat >"$FIX/docker_ps_a.txt" <<'EOF'
CONTAINER ID   IMAGE        STATUS                     NAMES
aaaa1111bbbb   demo:1.0     Exited (1) 2 minutes ago   demo
EOF

cat >"$FIX/docker_logs.txt" <<'EOF'
api: FATAL: startup aborted
EOF

cat >"$FIX/docker_inspect.txt" <<'EOF'
[{"Name":"/demo","State":{"ExitCode":1,"Status":"exited"}}]
EOF

cat >"$FIX/podman_ps_a.txt" <<'EOF'
CONTAINER ID  IMAGE       STATUS                     NAMES
cccc2222dddd  demo:1.0    Exited (1) 2 minutes ago   demo
EOF

# CRLF and no trailing newline: proves the bytes are copied, not re-printed.
printf 'State   Recv-Q  Send-Q  Local Address:Port\r\nLISTEN  0       128     0.0.0.0:8080' \
    >"$FIX/ss_tlnp.txt"

cat >"$FIX/ps_aux.txt" <<'EOF'
USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
demo      4242  0.0  0.1   1000   100 ?        S    10:00   0:00 demo-agent
EOF

cat >"$FIX/nginx_t.txt" <<'EOF'
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
EOF

cat >"$FIX/dig.txt" <<'EOF'
;; ANSWER SECTION:
example.test.   300   IN   A   192.0.2.10
EOF

# ---------------------------------------------------------------- dispatch
printf '\n== systemctl ==\n'
expect_fixture "systemctl status SERVICE -> systemctl_status.txt" systemctl_status.txt \
    systemctl status demo.service
expect_fixture "absolute argv[0] also dispatches on the basename" systemctl_status.txt \
    "$BIN_DIR/systemctl" status demo.service
expect_reject "systemctl with no arguments is rejected" 'unsupported invocation' systemctl
expect_reject "systemctl restart is not implemented" 'unsupported invocation' \
    systemctl restart demo.service
expect_reject "systemctl status without a unit is rejected" 'unsupported invocation' \
    systemctl status

printf '\n== journalctl ==\n'
expect_fixture "journalctl -xe -> journalctl_xe.txt" journalctl_xe.txt journalctl -xe
expect_fixture "journalctl -u UNIT -n 100 --no-pager -> journalctl_unit.txt" \
    journalctl_unit.txt journalctl -u demo.service -n 100 --no-pager
expect_fixture "journalctl accepts those three flags in any order" journalctl_unit.txt \
    journalctl --no-pager -n 100 -u demo.service
expect_reject "journalctl without a line count is rejected" 'unsupported invocation' \
    journalctl -u demo.service
expect_reject "journalctl only implements -n 100" 'unsupported invocation' \
    journalctl -u demo.service -n 50 --no-pager
expect_reject "journalctl -u with a missing value is rejected" 'unsupported invocation' \
    journalctl -u -n 100 --no-pager

printf '\n== kubectl ==\n'
expect_fixture "kubectl get pods -> kubectl_get_pods.txt" kubectl_get_pods.txt kubectl get pods
expect_fixture "kubectl get events -> kubectl_get_events.txt" kubectl_get_events.txt \
    kubectl get events
expect_fixture "kubectl get configmap NAME -> kubectl_get_configmap.txt" \
    kubectl_get_configmap.txt kubectl get configmap demo-config
expect_fixture "kubectl get deploy NAME -> kubectl_get_deploy.txt" kubectl_get_deploy.txt \
    kubectl get deploy demo
expect_fixture "kubectl describe pod NAME -> kubectl_describe_pod.txt" \
    kubectl_describe_pod.txt kubectl describe pod demo-aaaa-bbbb
expect_fixture "kubectl logs POD -> kubectl_logs.txt" kubectl_logs.txt kubectl logs demo-aaaa-bbbb
expect_reject "kubectl get pods with an extra argument is rejected" \
    'unsupported invocation' kubectl get pods --all-namespaces
expect_reject "kubectl get configmap without a name is rejected" \
    'unsupported invocation' kubectl get configmap
expect_reject "kubectl apply is not implemented" 'unsupported invocation' \
    kubectl apply -f demo.yaml

# The fixture name is a compile-time constant: an argument that looks like a
# path can never escape the fixture root or select another file.
expect_fixture "a traversal-shaped configmap name still selects the fixed fixture" \
    kubectl_get_configmap.txt kubectl get configmap ../../etc/passwd

printf '\n== docker and podman ==\n'
expect_fixture "docker ps -a -> docker_ps_a.txt" docker_ps_a.txt docker ps -a
expect_fixture "docker logs NAME -> docker_logs.txt" docker_logs.txt docker logs demo
expect_fixture "docker inspect NAME -> docker_inspect.txt" docker_inspect.txt docker inspect demo
expect_fixture "podman ps -a -> podman_ps_a.txt" podman_ps_a.txt podman ps -a
expect_reject "docker ps without -a is rejected" 'unsupported invocation' docker ps
expect_reject "docker run is not implemented" 'unsupported invocation' docker run demo

printf '\n== other mock commands ==\n'
expect_fixture "ss -tlnp keeps CRLF and the missing final newline" ss_tlnp.txt ss -tlnp
expect_fixture "ps aux -> ps_aux.txt" ps_aux.txt ps aux
expect_fixture "nginx -t -> nginx_t.txt" nginx_t.txt nginx -t
expect_fixture "dig takes its single fixture for any query" dig.txt dig example.test A
expect_reject "ss without -tlnp is rejected" 'unsupported invocation' ss -tulpn
expect_reject "nginx without -t is rejected" 'unsupported invocation' nginx -s reload

printf '\n== failure paths ==\n'
mkdir -p "$WORK/empty"
expect_reject "a missing fixture is an honest failure" 'cannot read' \
    env SREMOCK_ROOT="$WORK/empty" systemctl status demo.service
rm -f "$FIX/nginx_t.txt"
expect_reject "a removed fixture is reported, not silently substituted" 'cannot read' nginx -t
cat >"$FIX/nginx_t.txt" <<'EOF'
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
EOF

# ---------------------------------------------------------------- generator
printf '\n== generator (troubleshoot profile) ==\n'
GEN=$REPO_DIR/generator/generate.py
if ! command -v python3 >/dev/null 2>&1; then
    skip_ "python3 not found; generator integration not exercised"
elif [ ! -f "$GEN" ]; then
    skip_ "generator/generate.py missing; generator integration not exercised"
elif ! python3 "$GEN" --help 2>/dev/null | grep -q -- '--profile'; then
    skip_ "generator has no --profile support yet (pending T17 integration)"
else
    GEN_OUT=$WORK/gen
    GEN_ANSWERS=$WORK/answers.json
    # --set is still required by the CLI; --pick overrides the count and order.
    if python3 "$GEN" --seed "$SEED" --set 2 --profile troubleshoot \
            --pick svc-crashloop,k8s-crashloop \
            --out "$GEN_OUT" --dump-answers "$GEN_ANSWERS" >"$WORK/gen.log" 2>&1; then
        ok "generator --profile troubleshoot --pick ran (seed=$SEED)"
        fail_before=$fail
        python3 - "$GEN_OUT" "$GEN_ANSWERS" >"$WORK/gencheck.log" 2>&1 <<'PY'
import hashlib
import json
import os
import sys

out, answers_path = sys.argv[1], sys.argv[2]
failures = 0


def emit(ok, desc, detail=""):
    global failures
    if ok:
        print("PASS  " + desc)
    else:
        failures += 1
        print("FAIL  " + desc + (" :: " + detail if detail else ""))


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


# The T16 fixture contract: every name is always emitted, flat, nothing else.
FIXTURES = [
    "systemctl_status.txt", "journalctl_unit.txt", "journalctl_xe.txt",
    "ps_aux.txt", "ss_tlnp.txt", "curl_v.txt", "curl_I.txt", "df_h.txt",
    "free_m.txt", "top.txt", "du_sh.txt", "nginx_t.txt", "getenforce.txt",
    "ausearch_avc.txt", "dig.txt", "nslookup.txt", "ping.txt", "nc_zv.txt",
    "docker_ps_a.txt", "docker_logs.txt", "docker_inspect.txt",
    "podman_ps_a.txt", "podman_logs.txt", "podman_inspect.txt",
    "kubectl_get_pods.txt", "kubectl_describe_pod.txt", "kubectl_logs.txt",
    "kubectl_get_events.txt", "kubectl_get_configmap.txt", "kubectl_get_deploy.txt",
]
PICK = ["svc-crashloop", "k8s-crashloop"]

fixtures_dir = os.path.join(out, "fixtures")
present = sorted(os.listdir(fixtures_dir)) if os.path.isdir(fixtures_dir) else []
missing = [n for n in FIXTURES if n not in present]
extra = [n for n in present if n not in FIXTURES]
emit(not missing and not extra and len(present) == len(FIXTURES),
     "fixtures/ holds exactly the %d contracted files" % len(FIXTURES),
     "missing=%s extra=%s" % (missing, extra))

with open(answers_path, encoding="utf-8") as fh:
    dump = json.load(fh)

with open(os.path.join(out, "manifest"), encoding="utf-8") as fh:
    manifest = fh.read().splitlines()

emit(manifest[:2] == ["SEED " + str(dump["seed"]), "STAGES 2"],
     "manifest header has SEED and STAGES 2", repr(manifest[:2]))
emit(len(manifest) == 4, "manifest is exactly the 2-line header plus 2 stages",
     repr(manifest))
stage_lines = [line[len("STAGE "):] for line in manifest if line.startswith("STAGE ")]
emit(stage_lines == PICK, "manifest has exactly 2 STAGE lines in the picked order",
     repr(stage_lines))
emit(dump.get("order") == PICK, "answers dump order matches the picked order",
     repr(dump.get("order")))
emit(dump.get("set") == 2, "answers dump set size is 2", repr(dump.get("set")))

for index, sid in enumerate(PICK, 1):
    sd = os.path.join(out, "stages", "%02d" % index)

    with open(os.path.join(sd, "checks.tsv"), encoding="utf-8") as fh:
        blocks = [b for b in fh.read().split("\n\n") if b.strip()]
    checks = []
    for block in blocks:
        kv = {}
        for line in block.splitlines():
            key, _, value = line.partition("=")
            kv[key] = value
        checks.append(kv)

    emit(len(checks) == 3, "%s: checks.tsv defines 3 checks" % sid,
         "got %d" % len(checks))
    emit(any(c.get("type") == "file" for c in checks),
         "%s: at least one type=file check" % sid)
    emit(all(c.get("id") and c.get("type") in ("hash", "file") and c.get("fragment")
             for c in checks),
         "%s: every check has id, type and fragment" % sid)

    dumped = dump["stages"][sid]["checks"]
    emit([c.get("id") for c in checks] == [d.get("id") for d in dumped],
         "%s: dump check ids/order match checks.tsv" % sid,
         "%r vs %r" % ([c.get("id") for c in checks], [d.get("id") for d in dumped]))

    fragments = [c["fragment"] for c in checks]
    flag = "FLAG{" + sha_text("|".join(fragments))[:12] + "}"
    emit(flag == dump["stages"][sid]["flag"],
         "%s: dump flag recomputes from the checks.tsv fragments" % sid,
         "recomputed %s, dump %s" % (flag, dump["stages"][sid]["flag"]))
    with open(os.path.join(sd, "flag.sha256"), encoding="utf-8") as fh:
        flag_digest = fh.read().strip()
    emit(sha_text(flag) == flag_digest,
         "%s: flag.sha256 is the digest of the recomputed flag" % sid)

    for check in checks:
        by_id = {d["id"]: d for d in dumped}
        row = by_id[check["id"]]
        if check["type"] == "hash":
            emit(sha_text(row["answer"]) == check["answer"],
                 "%s/%s: dump answer hashes to the checks.tsv digest"
                 % (sid, check["id"]))
            continue

        shipped = os.path.join(out, "fs", check["path"].lstrip("/"))
        emit(os.path.isfile(shipped), "%s/%s: the checked file is shipped"
             % (sid, check["id"]), shipped)
        initial = sha_bytes(open(shipped, "rb").read())
        emit(initial != check["sha256"],
             "%s/%s: initial file differs from the expected repaired hash"
             % (sid, check["id"]), initial)
        emit(row.get("sha256") == check["sha256"],
             "%s/%s: dump sha256 matches checks.tsv" % (sid, check["id"]))
        emit(sha_text(row["answer"]) == check["sha256"],
             "%s/%s: dump repaired content hashes to the checked sha256"
             % (sid, check["id"]))

last = PICK[-1]
emit(dump["final_flag"] == dump["stages"][last]["flag"],
     "final flag is the last stage flag")
with open(os.path.join(out, "final.sha256"), encoding="utf-8") as fh:
    final_digest = fh.read().strip()
emit(sha_text(dump["final_flag"]) == final_digest,
     "final.sha256 is the digest of the final flag")

leaks = []
for dirpath, _dirnames, filenames in os.walk(os.path.join(out, "fs")):
    for name in filenames:
        if name == "answers.json":
            leaks.append(os.path.join(dirpath, name))
emit(not leaks, "no answer dump under fs/", repr(leaks))

sys.exit(1 if failures else 0)
PY
        gencheck_rc=$?
        while IFS= read -r line; do
            case $line in
                "PASS  "*) ok "${line#PASS  }" ;;
                "FAIL  "*) bad "${line#FAIL  }" ;;
                *) printf '%s\n' "$line" ;;
            esac
        done <"$WORK/gencheck.log"
        if [ "$gencheck_rc" -ne 0 ] && [ "$fail" -eq "$fail_before" ]; then
            bad "generator assertion script exited nonzero without reporting a failure"
        fi
    else
        bad "generator troubleshoot run failed"
        sed 's/^/        | /' "$WORK/gen.log"
    fi
fi

# ---------------------------------------------------------------- summary
printf '\n== summary ==\n'
printf 'passed: %d   failed: %d   skipped: %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ] || exit 1
printf 'OK\n'
