#!/bin/sh
# tests/troubleshoot_smoke.sh - smoke test for the troubleshoot profile.
#
# Part 1 (always runs): compile src/sremock.c statically, install it in a temp
# PATH under the command names it mocks, point SREMOCK_ROOT at a temp fixture
# directory, and assert the exact fixture bytes for systemctl, journalctl,
# kubectl, docker, podman and the resource-triage commands - plus the rejection
# paths and byte-for-byte passthrough.
#
# Part 2 (runs when the generator has --profile support): generate the default
# six-template pool and ordered PICK subsets, then assert the fixture contract,
# manifests, checks.tsv shape, stage/final flag recomputation, generated scenario
# clues and that no file check ships already solved. If the generator has no
# troubleshoot profile yet, part 2 is reported as pending (a SKIP, not a failure).
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

cat >"$FIX/podman_logs.txt" <<'EOF'
api: FATAL: startup aborted
EOF

cat >"$FIX/podman_inspect.txt" <<'EOF'
[{"Name":"demo","State":{"ExitCode":1,"Status":"exited"}}]
EOF

# CRLF and no trailing newline: proves the bytes are copied, not re-printed.
printf 'State   Recv-Q  Send-Q  Local Address:Port\r\nLISTEN  0       128     0.0.0.0:8080' \
    >"$FIX/ss_tlnp.txt"

cat >"$FIX/ps_aux.txt" <<'EOF'
USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
demo      4242  0.0  0.1   1000   100 ?        S    10:00   0:00 demo-agent
EOF

cat >"$FIX/df_h.txt" <<'EOF'
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda1        40G   18G   20G  48% /
/dev/vdb1        60G   60G     0 100% /var/log
EOF

cat >"$FIX/du_sh.txt" <<'EOF'
8.0K	/var/log/private
420M	/var/log/syslog
52G	/var/log/demo
EOF

cat >"$FIX/free_m.txt" <<'EOF'
               total        used        free      shared  buff/cache   available
Mem:           16000        4000        3000          80        9000       11500
Swap:           4096         512        3584
EOF

cat >"$FIX/top.txt" <<'EOF'
top - 10:00:00 up 12 days,  3:14,  1 user,  load average: 0.10, 0.20, 0.30
Tasks: 128 total,   1 running, 127 sleeping,   0 stopped,   0 zombie
EOF

cat >"$FIX/nginx_t.txt" <<'EOF'
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
EOF

cat >"$FIX/dig.txt" <<'EOF'
;; ANSWER SECTION:
example.test.   300   IN   A   192.0.2.10
EOF

cat >"$FIX/nslookup.txt" <<'EOF'
Server: 192.0.2.53
Name: example.test
Address: 192.0.2.10
EOF

cat >"$FIX/curl_I.txt" <<'EOF'
HTTP/1.1 502 Bad Gateway
Server: edge-demo
EOF

cat >"$FIX/curl_v.txt" <<'EOF'
*   Trying 192.0.2.10:8080...
* connect to stale.example.test (192.0.2.10) port 8080 failed: Connection refused
* connection conn-demo: refused
EOF

cat >"$FIX/ping.txt" <<'EOF'
PING stale.example.test (192.0.2.10) 56(84) bytes of data.
64 bytes from stale.example.test: icmp_seq=1 ttl=62 time=2.4 ms
EOF

cat >"$FIX/nc_zv.txt" <<'EOF'
nc: connect to stale.example.test port 8080 (tcp) failed: Connection refused
connection conn-demo: refused
EOF

cat >"$FIX/getenforce.txt" <<'EOF'
Permissive
EOF

cat >"$FIX/ausearch_avc.txt" <<'EOF'
----
<no matches>
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
expect_fixture "podman logs NAME -> podman_logs.txt" podman_logs.txt podman logs demo
expect_fixture "podman inspect NAME -> podman_inspect.txt" podman_inspect.txt podman inspect demo
expect_reject "docker ps without -a is rejected" 'unsupported invocation' docker ps
expect_reject "docker run is not implemented" 'unsupported invocation' docker run demo
expect_reject "podman ps without -a is rejected" 'unsupported invocation' podman ps
expect_reject "podman run is not implemented" 'unsupported invocation' podman run demo

printf '\n== other mock commands ==\n'
expect_fixture "ss -tlnp keeps CRLF and the missing final newline" ss_tlnp.txt ss -tlnp
expect_fixture "ps aux -> ps_aux.txt" ps_aux.txt ps aux
expect_fixture "df -h -> df_h.txt" df_h.txt df -h
expect_fixture "du -sh DIR -> du_sh.txt" du_sh.txt du -sh /var/log/demo
expect_fixture "free -m -> free_m.txt" free_m.txt free -m
expect_fixture "top -> top.txt" top.txt top
expect_fixture "nginx -t -> nginx_t.txt" nginx_t.txt nginx -t
expect_fixture "getenforce -> getenforce.txt" getenforce.txt getenforce
expect_fixture "ausearch -m avc -> ausearch_avc.txt" ausearch_avc.txt ausearch -m avc
expect_fixture "curl -v URL -> curl_v.txt" curl_v.txt curl -v http://example.test
expect_fixture "curl -I URL -> curl_I.txt" curl_I.txt curl -I http://example.test
expect_fixture "dig takes its single fixture for any query" dig.txt dig example.test A
expect_fixture "nslookup takes its single fixture for any query" nslookup.txt nslookup example.test
expect_fixture "ping takes its single fixture for any query" ping.txt ping -c 2 example.test
expect_fixture "nc -zv HOST PORT -> nc_zv.txt" nc_zv.txt nc -zv example.test 8080
expect_reject "ss without -tlnp is rejected" 'unsupported invocation' ss -tulpn
expect_reject "df without -h is rejected" 'unsupported invocation' df
expect_reject "du without -sh is rejected" 'unsupported invocation' du -s /var/log/demo
expect_reject "free without -m is rejected" 'unsupported invocation' free -h
expect_reject "top with arguments is rejected" 'unsupported invocation' top -b
expect_reject "nginx without -t is rejected" 'unsupported invocation' nginx -s reload
expect_reject "getenforce with arguments is rejected" 'unsupported invocation' getenforce -v
expect_reject "ausearch without -m avc is rejected" 'unsupported invocation' ausearch -m all
expect_reject "curl without a URL is rejected" 'unsupported invocation' curl -v
expect_reject "curl -s is not implemented" 'unsupported invocation' curl -s http://example.test
expect_reject "nc without -zv is rejected" 'unsupported invocation' nc -z example.test 8080

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
    cat >"$WORK/gencheck.py" <<'PY'
import hashlib
import json
import os
import re
import sys

out, answers_path, pick_arg = sys.argv[1], sys.argv[2], sys.argv[3]
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


def fixture(name):
    with open(os.path.join(out, "fixtures", name), encoding="utf-8") as fh:
        return fh.read()


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
POOL = ["svc-crashloop", "k8s-crashloop", "disk-full", "container-crash",
        "nginx-config", "dns-mismatch"]
NEUTRAL = "no data for this scenario\n"
PICK = pick_arg.split(",") if pick_arg else None
expected_count = len(PICK) if PICK else len(POOL)

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

emit(manifest[:2] == ["SEED " + str(dump["seed"]), "STAGES %d" % expected_count],
     "manifest header has SEED and STAGES %d" % expected_count, repr(manifest[:2]))
emit(len(manifest) == 2 + expected_count,
     "manifest is exactly the 2-line header plus %d stages" % expected_count,
     repr(manifest))
stage_lines = [line[len("STAGE "):] for line in manifest if line.startswith("STAGE ")]
if PICK:
    emit(stage_lines == PICK, "manifest has the picked stages in exact order", repr(stage_lines))
    emit(dump.get("order") == PICK, "answers dump order matches the picked order",
         repr(dump.get("order")))
else:
    emit(len(stage_lines) == len(POOL) and len(set(stage_lines)) == len(POOL)
         and set(stage_lines) == set(POOL),
         "default generation selects each of the six templates exactly once",
         repr(stage_lines))
    emit(dump.get("order") == stage_lines,
         "answers dump order matches the default manifest order", repr(dump.get("order")))
emit(dump.get("set") == expected_count, "answers dump set size is %d" % expected_count,
     repr(dump.get("set")))
emit(set(dump.get("stages", {})) == set(stage_lines),
     "answers dump contains exactly the selected stages", repr(sorted(dump.get("stages", {}))))

for index, sid in enumerate(stage_lines, 1):
    sd = os.path.join(out, "stages", "%02d" % index)
    if sid not in dump.get("stages", {}):
        emit(False, "%s: stage exists in the answers dump" % sid)
        continue

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
    with open(os.path.join(sd, "instruction"), encoding="utf-8") as fh:
        instruction = fh.read()
    emit(all(c.get("id") in instruction for c in checks),
         "%s: instruction names every check id" % sid)

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
        with open(shipped, "rb") as fh:
            initial = sha_bytes(fh.read())
        emit(initial != check["sha256"],
             "%s/%s: initial file differs from the expected repaired hash"
             % (sid, check["id"]), initial)
        emit(row.get("sha256") == check["sha256"],
             "%s/%s: dump sha256 matches checks.tsv" % (sid, check["id"]))
        emit(sha_text(row["answer"]) == check["sha256"],
             "%s/%s: dump repaired content hashes to the checked sha256"
             % (sid, check["id"]))

    if sid == "disk-full":
        df_text = fixture("df_h.txt")
        du_lines = [line for line in fixture("du_sh.txt").splitlines() if line]
        mount = dumped[0]["answer"]
        logdir = dumped[1]["answer"]
        emit(sum("100%" in line for line in df_text.splitlines()) == 1,
             "disk-full: df fixture has exactly one full filesystem")
        emit(any("100%" in line and line.split()[-1] == mount
                 for line in df_text.splitlines()),
             "disk-full: full filesystem row carries the checked mount")
        emit(bool(du_lines) and du_lines[-1].split("\t", 1)[-1] == logdir,
             "disk-full: largest du entry is the checked log directory")
        emit(all(fixture(name) != NEUTRAL
                 for name in ("df_h.txt", "du_sh.txt", "free_m.txt", "top.txt")),
             "disk-full: resource fixtures contain scenario data")

    if sid == "container-crash":
        name, exit_code = dumped[0]["answer"].rsplit(":", 1)
        missing_var = dumped[1]["answer"]
        states = []
        envs = []
        for engine in ("docker", "podman"):
            ps_text = fixture(engine + "_ps_a.txt")
            logs_text = fixture(engine + "_logs.txt")
            inspect = json.loads(fixture(engine + "_inspect.txt"))[0]
            states.append(inspect["State"])
            envs.append(inspect["Config"]["Env"])
            emit(name in ps_text and ("Exited (%s)" % exit_code) in ps_text,
                 "container-crash: %s ps fixture has the checked name and exit" % engine)
            emit(missing_var in logs_text,
                 "container-crash: %s logs fixture names the missing variable" % engine)
            emit(inspect.get("Name", "").lstrip("/") == name
                 and str(inspect["State"].get("ExitCode")) == exit_code
                 and missing_var not in inspect["Config"]["Env"],
                 "container-crash: %s inspect fixture confirms exit and missing env" % engine)
        emit(fixture("docker_logs.txt") == fixture("podman_logs.txt"),
             "container-crash: docker and podman log fixtures agree")
        emit(states[0].get("ExitCode") == states[1].get("ExitCode") and envs[0] == envs[1],
             "container-crash: docker and podman inspection facts agree")

    if sid == "nginx-config":
        error_fragment = dumped[0]["answer"]
        owner = dumped[1]["answer"]
        checked_path = dumped[2]["path"]
        fixed_text = dumped[2]["answer"]
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        initial_lines = initial_text.splitlines()
        fixed_lines = fixed_text.splitlines()
        changed = [(a, b) for a, b in zip(initial_lines, fixed_lines) if a != b]
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "nginx-config: two hash checks precede the file check")
        emit(owner == "nginx", "nginx-config: checked owner is the nginx service user")
        nginx_text = fixture("nginx_t.txt")
        line_match = re.fullmatch(r"nginx-L(\d+)-[0-9a-f]+", error_fragment)
        emit(line_match is not None and checked_path in nginx_text
             and error_fragment in nginx_text
             and (":" + line_match.group(1) in nginx_text if line_match else False),
             "nginx-config: nginx -t carries the checked path, line and error fragment")
        emit(len(initial_lines) == len(fixed_lines) and len(changed) == 2
             and sum(a + ";" == b for a, b in changed) == 1
             and sum(a.strip().startswith("server_name ")
                     and b.strip().startswith("server_name ") for a, b in changed) == 1,
             "nginx-config: broken-to-fixed diff is one semicolon and one server_name")
        reference_match = re.search(r"(/etc/nginx/references/[^\s`]+\.conf)", instruction)
        emit(reference_match is not None,
             "nginx-config: instruction names the service-owned reference asset")
        if reference_match:
            with open(os.path.join(out, "fs", reference_match.group(1).lstrip("/")),
                      encoding="utf-8") as fh:
                reference_text = fh.read()
            fixed_server = next((line.strip() for line in fixed_lines
                                 if line.strip().startswith("server_name ")), "")
            emit(bool(fixed_server) and fixed_server in reference_text,
                 "nginx-config: reference carries the corrected server_name")
        emit("nginx -t" in instruction and "ls -l" in instruction,
             "nginx-config: instruction teaches nginx -t and ls -l")
        emit(fixture("getenforce.txt") in ("Permissive\n", "Disabled\n")
             and fixture("ausearch_avc.txt") != NEUTRAL,
             "nginx-config: optional SELinux context is present and benign")

    if sid == "dns-mismatch":
        correct_host = dumped[0]["answer"]
        connection_fragment = dumped[1]["answer"]
        checked_path = dumped[2]["path"]
        fixed_text = dumped[2]["answer"]
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        url_match = re.search(r"endpoint_url=http://([^:]+):(\d+)(/v1)", initial_text)
        wrong_host = url_match.group(1) if url_match else ""
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "dns-mismatch: two hash checks precede the file check")
        emit(bool(wrong_host) and wrong_host != correct_host
             and wrong_host in initial_text and correct_host not in initial_text
             and fixed_text == initial_text.replace(wrong_host, correct_host),
             "dns-mismatch: repair changes only the stale endpoint hostname")
        emit(all(correct_host in fixture(name) and wrong_host in fixture(name)
                 for name in ("dig.txt", "nslookup.txt")),
             "dns-mismatch: DNS fixtures distinguish canonical and stale hosts")
        emit("502" in fixture("curl_I.txt") and wrong_host in fixture("curl_I.txt"),
             "dns-mismatch: curl -I shows 502 from the stale host")
        emit(connection_fragment in fixture("curl_v.txt")
             and connection_fragment in fixture("nc_zv.txt")
             and "Connection refused" in fixture("nc_zv.txt"),
             "dns-mismatch: transport fixtures share the checked connection fragment")
        ping_text = fixture("ping.txt")
        emit(wrong_host in ping_text and "0% packet loss" in ping_text
             and "%%" not in ping_text,
             "dns-mismatch: ping corroborates the stale host and renders packet loss literally")
        emit(all(fixture(name) != NEUTRAL for name in
                 ("dig.txt", "nslookup.txt", "curl_I.txt", "curl_v.txt",
                  "ping.txt", "nc_zv.txt")),
             "dns-mismatch: all DNS and transport fixtures contain scenario data")
        emit(all(command in instruction for command in
                 ("dig", "nslookup", "curl -I", "curl -v", "ping", "nc -zv")),
             "dns-mismatch: instruction teaches the DNS and transport checklist")

last = stage_lines[-1] if stage_lines else None
emit(last is not None and dump["final_flag"] == dump["stages"][last]["flag"],
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

    run_generator_case() {
        gc_desc=$1
        gc_label=$2
        gc_set=$3
        gc_pick=${4:-}
        gc_out=$WORK/gen-$gc_label
        gc_answers=$WORK/answers-$gc_label.json
        gc_log=$WORK/gen-$gc_label.log
        gc_check_log=$WORK/gencheck-$gc_label.log

        # --set is required by the CLI; --pick overrides its count and order.
        if [ -n "$gc_pick" ]; then
            if python3 "$GEN" --seed "$SEED" --set "$gc_set" --profile troubleshoot \
                    --pick "$gc_pick" --out "$gc_out" --dump-answers "$gc_answers" \
                    >"$gc_log" 2>&1; then
                gc_rc=0
            else
                gc_rc=$?
            fi
        else
            if python3 "$GEN" --seed "$SEED" --set "$gc_set" --profile troubleshoot \
                    --out "$gc_out" --dump-answers "$gc_answers" >"$gc_log" 2>&1; then
                gc_rc=0
            else
                gc_rc=$?
            fi
        fi

        if [ "$gc_rc" -eq 0 ]; then
            ok "$gc_desc ran (seed=$SEED)"
            fail_before=$fail
            python3 "$WORK/gencheck.py" "$gc_out" "$gc_answers" "$gc_pick" \
                >"$gc_check_log" 2>&1
            gencheck_rc=$?
            while IFS= read -r line; do
                case $line in
                    "PASS  "*) ok "$gc_label: ${line#PASS  }" ;;
                    "FAIL  "*) bad "$gc_label: ${line#FAIL  }" ;;
                    *) printf '%s\n' "$line" ;;
                esac
            done <"$gc_check_log"
            if [ "$gencheck_rc" -ne 0 ] && [ "$fail" -eq "$fail_before" ]; then
                bad "$gc_label: generator assertion script exited nonzero without reporting a failure"
            fi
        else
            bad "$gc_desc failed (rc=$gc_rc)"
            sed 's/^/        | /' "$gc_log"
        fi
    }

    run_generator_case "default six-template troubleshoot generation" default 6
    run_generator_case "original two-template PICK" pilot-pick 2 \
        svc-crashloop,k8s-crashloop
    # A reversed, non-pool order and a deliberately different SET prove PICK
    # controls both the exact subset order and the effective stage count.
    run_generator_case "new two-template PICK with reversed order" new-pick 1 \
        container-crash,disk-full
    run_generator_case "wave-three two-template PICK" wave3-pick 1 \
        nginx-config,dns-mismatch
    run_generator_case "wave-three PICK in reversed order" wave3-reversed 6 \
        dns-mismatch,nginx-config
fi

# ---------------------------------------------------------------- summary
printf '\n== summary ==\n'
printf 'passed: %d   failed: %d   skipped: %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ] || exit 1
printf 'OK\n'
