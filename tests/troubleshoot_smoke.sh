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
# sixteen-template pool and ordered PICK subsets, then assert the fixture contract,
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
INCLUDE_DIR=$WORK/include
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
mkdir -p "$INCLUDE_DIR"
cat >"$INCLUDE_DIR/valid_names.inc" <<'EOF'
/* Fixed host-smoke names; generated real-name tables are tested in part 2. */
static const ValidName valid_names[] = {
    {"unit", "demo.service"},
    {"pod", "demo-aaaa-bbbb"},
    {"cm", "demo-config"},
    {"svc", "demo-service"},
    {"deploy", "demo"},
    {"container", "demo"},
    {NULL, NULL},
};
#define VALID_NAME_COUNT (sizeof(valid_names) / sizeof(valid_names[0]) - 1)
EOF
if [ "${SRE_SMOKE_SKIP_BUILD:-0}" = "1" ] && [ -x "$BIN" ]; then
    ok "using prebuilt binary at $BIN (SRE_SMOKE_SKIP_BUILD=1)"
elif gcc -O2 -static -Wall -Wextra -Werror -DSREMOCK_VALID_NAMES=1 \
        -I"$INCLUDE_DIR" -o "$BIN" "$REPO_DIR/src/sremock.c" >"$BUILD_LOG" 2>&1; then
    ok "static build of src/sremock.c with a compiled name table"
else
    bad "static build failed"
    sed 's/^/        | /' "$BUILD_LOG"
    exit 1
fi
if gcc -O2 -static -Wall -Wextra -Werror -o "$WORK/sremock-empty" \
        "$REPO_DIR/src/sremock.c" >"$WORK/empty-build.log" 2>&1; then
    ok "standalone build uses the empty-table fallback"
else
    bad "standalone empty-table build failed"
    sed 's/^/        | /' "$WORK/empty-build.log"
    exit 1
fi
if [ -s "$BUILD_LOG" ]; then
    note "compiler diagnostics:"
    sed 's/^/        | /' "$BUILD_LOG"
fi

# ---------------------------------------------------------------- harness
mkdir -p "$FIX" "$BIN_DIR" "$WORK/empty-bin"
ln -s "$WORK/sremock-empty" "$WORK/empty-bin/systemctl"
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

# expect_error <desc> <expected-rc> <exact-stderr> <cmd...>
# A nonexistent fixture root proves name validation happens before fixture I/O.
expect_error() {
    ee_desc=$1
    ee_rc=$2
    ee_stderr=$3
    shift 3
    printf '%s\n' "$ee_stderr" >"$WORK/expected-err"
    env SREMOCK_ROOT="$WORK/no-fixtures" "$@" >"$WORK/out" 2>"$WORK/err"
    ee_actual=$?
    if [ "$ee_actual" -eq "$ee_rc" ] && cmp -s "$WORK/err" "$WORK/expected-err" \
            && [ ! -s "$WORK/out" ]; then
        ok "$ee_desc (rc=$ee_actual, exact stderr)"
    else
        bad "$ee_desc (rc=$ee_actual, expected rc=$ee_rc and exact stderr)"
        note "stdout:"; sed 's/^/        | /' "$WORK/out"
        note "stderr:"; sed 's/^/        | /' "$WORK/err"
    fi
}

# expect_stdout <desc> <expected-rc> <exact-stdout> <cmd...>
expect_stdout() {
    es_desc=$1
    es_rc=$2
    es_stdout=$3
    shift 3
    printf '%s\n' "$es_stdout" >"$WORK/expected-out"
    env SREMOCK_ROOT="$WORK/no-fixtures" "$@" >"$WORK/out" 2>"$WORK/err"
    es_actual=$?
    if [ "$es_actual" -eq "$es_rc" ] && cmp -s "$WORK/out" "$WORK/expected-out" \
            && [ ! -s "$WORK/err" ]; then
        ok "$es_desc (rc=$es_actual, exact stdout)"
    else
        bad "$es_desc (rc=$es_actual, expected rc=$es_rc and exact stdout)"
        note "stdout:"; sed 's/^/        | /' "$WORK/out"
        note "stderr:"; sed 's/^/        | /' "$WORK/err"
    fi
}

# expect_help <desc> <marker> <cmd...>
expect_help() {
    eh_desc=$1
    eh_marker=$2
    shift 2
    env SREMOCK_ROOT="$WORK/no-fixtures" "$@" >"$WORK/out" 2>"$WORK/err"
    eh_rc=$?
    if [ "$eh_rc" -eq 0 ] && [ -s "$WORK/out" ] \
            && grep -F -- "$eh_marker" "$WORK/out" >/dev/null 2>&1 \
            && [ ! -s "$WORK/err" ]; then
        ok "$eh_desc (no fixture read)"
    else
        bad "$eh_desc (rc=$eh_rc, need marker '$eh_marker', empty stderr, no fixture read)"
        note "stdout:"; sed 's/^/        | /' "$WORK/out"
        note "stderr:"; sed 's/^/        | /' "$WORK/err"
    fi
}

# expect_help_without <desc> <marker> <forbidden> <cmd...>
expect_help_without() {
    ehw_desc=$1
    ehw_marker=$2
    ehw_forbidden=$3
    shift 3
    env SREMOCK_ROOT="$WORK/no-fixtures" "$@" >"$WORK/out" 2>"$WORK/err"
    ehw_rc=$?
    if [ "$ehw_rc" -eq 0 ] && grep -F -- "$ehw_marker" "$WORK/out" >/dev/null 2>&1 \
            && ! grep -F -- "$ehw_forbidden" "$WORK/out" >/dev/null 2>&1 \
            && [ ! -s "$WORK/err" ]; then
        ok "$ehw_desc"
    else
        bad "$ehw_desc (rc=$ehw_rc, forbidden '$ehw_forbidden' or missing '$ehw_marker')"
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

cat >"$FIX/kubectl_get_svc.txt" <<'EOF'
NAME          TYPE        CLUSTER-IP    EXTERNAL-IP   PORT(S)   AGE   SELECTOR
demo-service  ClusterIP   10.0.0.20    <none>        80/TCP    7m    app=demo
EOF

cat >"$FIX/kubectl_get_endpoints.txt" <<'EOF'
NAME          ENDPOINTS   AGE
demo-service  <none>      7m
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
expect_fixture "systemctl status UNIT.service -> systemctl_status.txt" systemctl_status.txt \
    systemctl status demo.service
expect_fixture "systemctl unit suffix is optional" systemctl_status.txt \
    systemctl status demo
expect_fixture "absolute argv[0] also dispatches on the basename" systemctl_status.txt \
    "$BIN_DIR/systemctl" status demo.service
expect_error "systemctl missing unit has real stderr and exit 4" 4 \
    'Unit missing.service could not be found.' systemctl status missing
expect_error "systemctl does not double the .service suffix" 4 \
    'Unit missing.service could not be found.' systemctl status missing.service
expect_error "standalone fallback table treats every unit as absent" 4 \
    'Unit demo.service could not be found.' "$WORK/empty-bin/systemctl" status demo
expect_reject "systemctl with no arguments is rejected" 'unsupported invocation' systemctl
expect_reject "systemctl restart is not implemented" 'unsupported invocation' \
    systemctl restart demo.service
expect_reject "systemctl status without a unit is rejected" 'unsupported invocation' \
    systemctl status

printf '\n== journalctl ==\n'
expect_fixture "journalctl -xe -> journalctl_xe.txt" journalctl_xe.txt journalctl -xe
expect_fixture "journalctl -x -e is the same shorthand" journalctl_xe.txt journalctl -x -e
expect_fixture "journalctl -ex is the same shorthand" journalctl_xe.txt journalctl -ex
expect_fixture "journalctl -u UNIT -n 100 --no-pager -> journalctl_unit.txt" \
    journalctl_unit.txt journalctl -u demo.service -n 100 --no-pager
expect_fixture "journalctl accepts long option aliases" journalctl_unit.txt \
    journalctl --unit=demo --lines=100 --no-pager
expect_fixture "journalctl accepts long options with separate values" journalctl_unit.txt \
    journalctl --unit demo.service --lines 100 --no-pager
expect_fixture "journalctl accepts those flags in any order" journalctl_unit.txt \
    journalctl --no-pager -n 100 -u demo.service
expect_stdout "journalctl unknown unit stays a successful empty journal" 0 '-- No entries --' \
    journalctl --unit=missing.service --lines=100 --no-pager
expect_reject "journalctl without a line count is rejected" 'unsupported invocation' \
    journalctl -u demo.service
expect_reject "journalctl only implements -n 100" 'unsupported invocation' \
    journalctl -u demo.service -n 50 --no-pager
expect_reject "journalctl -u with a missing value is rejected" 'unsupported invocation' \
    journalctl -u -n 100 --no-pager

printf '\n== kubectl ==\n'
expect_fixture "kubectl get pods bare list form" kubectl_get_pods.txt kubectl get pods
expect_fixture "kubectl get pod bare singular list alias" kubectl_get_pods.txt kubectl get pod
expect_fixture "kubectl get po bare shorthand list alias" kubectl_get_pods.txt kubectl get po
expect_fixture "kubectl resource types are case-insensitive" kubectl_get_pods.txt kubectl get PoD
expect_fixture "kubectl get pod NAME validates a real pod" kubectl_get_pods.txt \
    kubectl get pod demo-aaaa-bbbb
expect_fixture "kubectl TYPE/NAME selector" kubectl_get_pods.txt \
    kubectl get pods/demo-aaaa-bbbb
expect_fixture "kubectl get configmap bare singular list alias" kubectl_get_configmap.txt \
    kubectl get configmap
expect_fixture "kubectl get configmaps bare plural list form" kubectl_get_configmap.txt \
    kubectl get configmaps
expect_fixture "kubectl get cm bare shorthand list alias" kubectl_get_configmap.txt \
    kubectl get cm
expect_fixture "kubectl get cm NAME shorthand" kubectl_get_configmap.txt \
    kubectl get cm demo-config
expect_fixture "kubectl plural ConfigMaps TYPE/NAME selector" kubectl_get_configmap.txt \
    kubectl get ConfigMaps/demo-config
expect_fixture "kubectl get svc bare shorthand list alias" kubectl_get_svc.txt kubectl get svc
expect_fixture "kubectl get service bare singular list alias" kubectl_get_svc.txt kubectl get service
expect_fixture "kubectl get services bare plural list form" kubectl_get_svc.txt kubectl get services
expect_fixture "kubectl get services NAME" kubectl_get_svc.txt \
    kubectl get services demo-service
expect_fixture "kubectl get service/NAME selector" kubectl_get_svc.txt \
    kubectl get service/demo-service
expect_fixture "kubectl get ep bare shorthand list alias" kubectl_get_endpoints.txt kubectl get ep
expect_fixture "kubectl get endpoint bare singular list alias" kubectl_get_endpoints.txt \
    kubectl get endpoint
expect_fixture "kubectl get endpoints bare plural list form" kubectl_get_endpoints.txt \
    kubectl get endpoints
expect_fixture "kubectl get endpoints NAME" kubectl_get_endpoints.txt \
    kubectl get endpoints demo-service
expect_fixture "kubectl get endpoint/NAME selector" kubectl_get_endpoints.txt \
    kubectl get endpoint/demo-service
expect_fixture "kubectl get ev bare shorthand list alias" kubectl_get_events.txt kubectl get ev
expect_fixture "kubectl get event bare singular list alias" kubectl_get_events.txt kubectl get event
expect_fixture "kubectl get events bare plural list form" kubectl_get_events.txt \
    kubectl get events
expect_fixture "kubectl get deploy bare shorthand list alias" kubectl_get_deploy.txt kubectl get deploy
expect_fixture "kubectl get deployment bare singular list alias" kubectl_get_deploy.txt \
    kubectl get deployment
expect_fixture "kubectl get deployments bare plural list form" kubectl_get_deploy.txt \
    kubectl get deployments
expect_fixture "kubectl get deployment NAME" kubectl_get_deploy.txt \
    kubectl get deployment demo
expect_fixture "kubectl get deployments/NAME selector" kubectl_get_deploy.txt \
    kubectl get deployments/demo
expect_fixture "kubectl describe pod NAME" kubectl_describe_pod.txt \
    kubectl describe pod demo-aaaa-bbbb
expect_fixture "kubectl describe po TYPE/NAME" kubectl_describe_pod.txt \
    kubectl describe po/demo-aaaa-bbbb
expect_fixture "kubectl logs POD" kubectl_logs.txt kubectl logs demo-aaaa-bbbb
expect_fixture "kubectl logs pod/POD" kubectl_logs.txt kubectl logs pod/demo-aaaa-bbbb
expect_fixture "kubectl common short flags" kubectl_get_pods.txt \
    kubectl get po -n demo -o wide
expect_fixture "kubectl common long flags" kubectl_get_pods.txt \
    kubectl get pods --namespace=demo --output=wide
expect_fixture "kubectl all-namespaces short flag" kubectl_get_pods.txt \
    kubectl get pods -A
expect_fixture "kubectl all-namespaces long flag and global flag order" kubectl_get_pods.txt \
    kubectl --all-namespaces get pods
expect_error "kubectl missing pod uses canonical plural and exit 1" 1 \
    'Error from server (NotFound): pods "missing" not found' kubectl get po missing
expect_error "kubectl TYPE/NAME miss uses canonical plural" 1 \
    'Error from server (NotFound): configmaps "missing" not found' kubectl get cm/missing
expect_error "kubectl missing Service name" 1 \
    'Error from server (NotFound): services "missing" not found' kubectl get svc missing
expect_error "kubectl missing endpoint name" 1 \
    'Error from server (NotFound): endpoints "missing" not found' kubectl get ep missing
expect_error "kubectl missing deployment name" 1 \
    'Error from server (NotFound): deployments "missing" not found' kubectl get deploy missing
expect_error "kubectl describe miss reports pods" 1 \
    'Error from server (NotFound): pods "missing" not found' kubectl describe pod missing
expect_error "kubectl logs miss reports pods" 1 \
    'Error from server (NotFound): pods "missing" not found' kubectl logs missing
expect_error "traversal-shaped names are rejected before fixture I/O" 1 \
    'Error from server (NotFound): configmaps "../../etc/passwd" not found' \
    kubectl get configmap ../../etc/passwd
expect_reject "kubectl get svc with --watch is rejected" \
    'unsupported invocation' kubectl get svc demo --watch
expect_reject "kubectl TYPE/NAME plus a separate name is rejected" \
    'unsupported invocation' kubectl get pod/demo-aaaa-bbbb extra
expect_reject "kubectl named events are outside the mock surface" \
    'unsupported invocation' kubectl get events demo
expect_reject "kubectl apply is not implemented" 'unsupported invocation' \
    kubectl apply -f demo.yaml

printf '\n== docker and podman ==\n'
expect_fixture "docker ps -a -> docker_ps_a.txt" docker_ps_a.txt docker ps -a
expect_fixture "docker ps --all" docker_ps_a.txt docker ps --all
expect_fixture "docker container ls -a alias" docker_ps_a.txt docker container ls -a
expect_fixture "docker container list --all alias" docker_ps_a.txt docker container list --all
expect_fixture "docker container ps -a alias" docker_ps_a.txt docker container ps -a
expect_fixture "docker logs NAME" docker_logs.txt docker logs demo
expect_fixture "docker container logs NAME alias" docker_logs.txt docker container logs demo
expect_fixture "docker inspect NAME" docker_inspect.txt docker inspect demo
expect_fixture "docker container inspect NAME alias" docker_inspect.txt docker container inspect demo
expect_fixture "podman ps -a -> podman_ps_a.txt" podman_ps_a.txt podman ps -a
expect_fixture "podman container ps --all alias" podman_ps_a.txt podman container ps --all
expect_fixture "podman container ls -a alias" podman_ps_a.txt podman container ls -a
expect_fixture "podman container list -a alias" podman_ps_a.txt podman container list -a
expect_fixture "podman logs NAME" podman_logs.txt podman logs demo
expect_fixture "podman container logs NAME alias" podman_logs.txt podman container logs demo
expect_fixture "podman inspect NAME" podman_inspect.txt podman inspect demo
expect_fixture "podman container inspect NAME alias" podman_inspect.txt podman container inspect demo
expect_error "docker missing container has real stderr and exit 1" 1 \
    'Error: No such container: missing' docker logs missing
expect_error "podman missing container has real stderr and exit 1" 1 \
    'Error: No such container: missing' podman container inspect missing
expect_reject "docker ps without -a is rejected" 'unsupported invocation' docker ps
expect_reject "docker run is not implemented" 'unsupported invocation' docker run demo
expect_reject "podman ps without -a is rejected" 'unsupported invocation' podman ps
expect_reject "podman run is not implemented" 'unsupported invocation' podman run demo
expect_reject "container logs with extra operands is rejected" 'unsupported invocation' \
    docker logs demo extra

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
expect_reject "free with an unsupported flag is rejected" 'unsupported invocation' free -x
expect_reject "top with arguments is rejected" 'unsupported invocation' top -b
expect_reject "nginx without -t is rejected" 'unsupported invocation' nginx -s reload
expect_reject "getenforce with arguments is rejected" 'unsupported invocation' getenforce -v
expect_reject "ausearch without -m avc is rejected" 'unsupported invocation' ausearch -m all
expect_reject "curl without a URL is rejected" 'unsupported invocation' curl -v
expect_reject "curl -s is not implemented" 'unsupported invocation' curl -s http://example.test
expect_reject "nc without -zv is rejected" 'unsupported invocation' nc -z example.test 8080

printf '\n== help ==\n'
expect_help "sremock --help lists the dispatcher commands" 'kubectl' "$BIN" --help
expect_help "sremock -h is global help" 'systemctl' "$BIN" -h
for command in $MOCK_COMMANDS; do
    expect_help "$command --help works without fixtures" '--help' "$command" --help
done
expect_help "kubectl help has Usage/Aliases/Options styling" 'Aliases:' kubectl --help
expect_help "kubectl help documents bare ConfigMap lists" 'kubectl get configmaps' kubectl --help
expect_help "kubectl help documents bare Deployment lists" 'kubectl get deployments' kubectl --help
expect_help_without "kubectl help omits unsupported watch/apply forms" 'Options:' '--watch' \
    kubectl --help
expect_help_without "kubectl help does not advertise apply" 'Usage:' 'kubectl apply' \
    kubectl -h
expect_help "systemctl help uses man-page sections" 'SYSTEMCTL(1)' systemctl --help
expect_help "systemctl help documents optional unit suffix" 'UNIT.service' systemctl -h
expect_help_without "systemctl help omits unsupported verbs" 'SYNOPSIS' 'restart' \
    systemctl --help
expect_help "journalctl help uses man-page sections" 'JOURNALCTL(1)' journalctl --help
expect_help "journalctl help documents long flag aliases" '--unit=UNIT' journalctl -h
expect_help_without "journalctl help omits unsupported filters" 'OPTIONS' '--since' \
    journalctl --help
expect_help "docker help has Usage/Aliases/Description/Options styling" 'Description:' \
    docker --help
expect_help "docker help renders ps aliases from the alias table" 'container list' docker -h
expect_help_without "docker help omits unsupported run forms" 'Aliases:' 'docker run' \
    docker --help
expect_help "podman help has container aliases" 'container ps' podman --help
expect_help "curl help uses curl-style Usage/Options" 'Usage: curl -v URL' curl --help
expect_help_without "curl help omits unsupported silent mode" 'Options:' '  -s ' curl -h
expect_help "coreutils-style help shows the exact supported form" 'Usage: ps aux' ps --help
expect_help "df --help is available without fixtures" 'Usage: df -h' df --help
expect_help "df --help describes -h as human-readable" '-h, --human-readable' df --help
expect_help "df --help names only the long help flag" \
    '--help                display this help and exit' df --help
expect_help_without "df --help does not claim -h displays help" 'Options:' '-h, --help' df --help
expect_help "df accepts a help request alongside its operational -h flag" 'Usage: df -h' df ignored -h
expect_help "-h anywhere wins for non-df commands" 'Usage:' kubectl get -h
expect_help "--help anywhere wins over invalid command syntax" 'SYSTEMCTL(1)' systemctl status --help demo
expect_help "container help anywhere wins" 'Usage:' docker container --help ls
expect_help "curl help anywhere wins" 'Usage:' curl -v --help

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


def snapshot(name, label):
    marker = "# snapshot " + label + "\n"
    text = fixture(name)
    if marker not in text:
        return ""
    return text.split(marker, 1)[1].split("\n\n# snapshot ", 1)[0].strip() + "\n"


def labelled_report(name, label):
    marker = "# " + label + "\n"
    text = fixture(name)
    if marker not in text:
        return ""
    return text.split(marker, 1)[1].split("\n\n# ", 1)[0].strip() + "\n"


# The T16 fixture contract: every name is always emitted, flat, nothing else.
FIXTURES = [
    "systemctl_status.txt", "journalctl_unit.txt", "journalctl_xe.txt",
    "ps_aux.txt", "ss_tlnp.txt", "curl_v.txt", "curl_I.txt", "df_h.txt",
    "free_m.txt", "top.txt", "du_sh.txt", "nginx_t.txt", "getenforce.txt",
    "ausearch_avc.txt", "dig.txt", "nslookup.txt", "ping.txt", "nc_zv.txt",
    "docker_ps_a.txt", "docker_logs.txt", "docker_inspect.txt",
    "podman_ps_a.txt", "podman_logs.txt", "podman_inspect.txt",
    "kubectl_get_pods.txt", "kubectl_get_svc.txt", "kubectl_get_endpoints.txt",
    "kubectl_describe_pod.txt", "kubectl_logs.txt", "kubectl_get_events.txt",
    "kubectl_get_configmap.txt", "kubectl_get_deploy.txt",
]
POOL = ["svc-crashloop", "k8s-crashloop", "disk-full", "container-crash",
        "nginx-config", "dns-mismatch", "selinux-avc", "port-conflict",
        "oom-kill", "image-pull", "svc-selector", "resource-limits",
        "tls-expiry", "dependency-down", "blocked-port", "perm-denied"]
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
emit(all("%%" not in fixture(name) for name in FIXTURES),
     "no fixture contains a literal double percent")

# Build-time name validation table: only selected-template keys, stable order,
# unique real names, and no copy in the runtime fixture/fs trees.
NAME_KEYS = ["unit", "pod", "cm", "svc", "deploy", "container"]
NAME_KEYS_BY_TEMPLATE = {
    "svc-crashloop": {"unit"},
    "k8s-crashloop": {"pod", "cm", "deploy"},
    "disk-full": {"unit"},
    "container-crash": {"container"},
    "svc-selector": {"pod", "svc", "deploy"},
    "resource-limits": {"pod", "deploy"},
    "dependency-down": {"unit"},
    "perm-denied": {"unit"},
    "oom-kill": {"unit"},
    "image-pull": {"container"},
}
with open(os.path.join(out, "valid_names.inc"), encoding="utf-8") as fh:
    names_text = fh.read()
name_rows = re.findall(r'^\s*\{"(unit|pod|cm|svc|deploy|container)", "([^"]+)"\},$',
                       names_text, re.MULTILINE)
expected_keys = set()
for sid in (PICK or POOL):
    expected_keys.update(NAME_KEYS_BY_TEMPLATE.get(sid, set()))
actual_keys = {key for key, _name in name_rows}
emit(actual_keys == expected_keys,
     "valid_names.inc has exactly the selected templates' resource keys",
     "expected=%s actual=%s" % (sorted(expected_keys), sorted(actual_keys)))
expected_rows = sorted(name_rows, key=lambda row: (NAME_KEYS.index(row[0]), row[1]))
emit(name_rows == expected_rows and len(name_rows) == len(set(name_rows)),
     "valid_names.inc rows are unique and in canonical key/name order")
all_fixtures = "\n".join(fixture(name) for name in FIXTURES)
all_fs_paths = "\n".join(
    os.path.join(dirpath, filename)
    for dirpath, _dirnames, filenames in os.walk(os.path.join(out, "fs"))
    for filename in filenames
)
missing_names = []
for key, name in name_rows:
    candidate = name[:-8] if key == "unit" and name.endswith(".service") else name
    if name not in all_fixtures and candidate not in all_fs_paths:
        missing_names.append((key, name))
emit(not missing_names,
     "every compiled valid name occurs in selected scenario fixtures or files",
     repr(missing_names))
emit("valid_names.inc" not in present
     and not os.path.exists(os.path.join(out, "fs", "valid_names.inc")),
     "valid_names.inc stays outside runtime fixture/fs data")

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
         "default generation selects each of the sixteen templates exactly once",
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
    with open(os.path.join(sd, "hint"), encoding="utf-8") as fh:
        hint_text = fh.read()
    emit(hint_text.strip() != "" and "%%" not in instruction and "%%" not in hint_text,
         "%s: hint is non-empty and stage prose has no literal double percent" % sid)
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

    if sid == "svc-crashloop":
        svc_cfg = dumped[1]["path"]
        with open(os.path.join(out, "fs", svc_cfg.lstrip("/")),
                  encoding="utf-8") as fh:
            svc_initial = fh.read()
        svc_fixed = dumped[1]["answer"]
        svc_held = re.search(r"bind_port = (\d+)", svc_initial)
        svc_free = re.search(r"bind_port = (\d+)", svc_fixed)
        emit(svc_held is not None and svc_free is not None
             and dumped[2]["answer"] in fixture("ss_tlnp.txt")
             and svc_held.group(1) in fixture("ss_tlnp.txt"),
             "svc-crashloop: composed ss evidence retains its holder and port")
        emit(svc_free is not None
             and ("reserved replacement port is " + svc_free.group(1))
             in fixture("journalctl_unit.txt"),
             "svc-crashloop: journal retains its reserved free-port clue")

    if sid == "k8s-crashloop":
        k8s_checks = dumped
        pod_reason, missing_key = k8s_checks[0]["answer"], k8s_checks[1]["answer"]
        k8s_pod = pod_reason.split(":", 1)[0]
        actual_key_match = re.search(r"^\s+key: (\S+)$", k8s_checks[2]["answer"],
                                     re.MULTILINE)
        actual_key = actual_key_match.group(1) if actual_key_match else ""
        pods_text = fixture("kubectl_get_pods.txt")
        describe = fixture("kubectl_describe_pod.txt")
        logs = fixture("kubectl_logs.txt")
        events = fixture("kubectl_get_events.txt")
        deploy = fixture("kubectl_get_deploy.txt")
        emit(k8s_pod in pods_text and pod_reason.split(":", 1)[1] in logs
             and missing_key in logs and actual_key
             in fixture("kubectl_get_configmap.txt"),
             "k8s-crashloop: merged pod/log/configmap fixtures retain its clues")
        emit(k8s_pod in describe and k8s_pod in events
             and os.path.basename(k8s_checks[2]["path"])[:-5] in deploy,
             "k8s-crashloop: merged describe/event/deploy fixtures retain its pod")

    if sid == "svc-selector":
        svc_checks = dumped
        service = svc_checks[0]["answer"]
        mismatch = svc_checks[1]["answer"]
        checked_path = svc_checks[2]["path"]
        fixed_text = svc_checks[2]["answer"]
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        svc_text = fixture("kubectl_get_svc.txt")
        endpoints = fixture("kubectl_get_endpoints.txt")
        events = fixture("kubectl_get_events.txt")
        curl = fixture("curl_v.txt")
        service_row = next((line for line in svc_text.splitlines()
                            if re.match(r"^" + re.escape(service) + r"\s", line)), "")
        fields = service_row.split()
        selector_match = re.search(r"app=([^\s]+)", service_row)
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "svc-selector: two hash checks precede the file check")
        emit(bool(fields) and "ClusterIP" in fields and selector_match is not None
             and selector_match.group(1) == service,
             "svc-selector: get svc names the checked Service and its selector")
        emit(mismatch in endpoints and mismatch in events and "<none>" in endpoints
             and "EndpointsNotFound" in events,
             "svc-selector: endpoint/event evidence shares the mismatch fragment")
        emit(len(fields) >= 5 and fields[2] in curl
             and fields[4].split("/", 1)[0] in curl and "Connection refused" in curl,
             "svc-selector: curl refuses the Service cluster address")
        expected_fixed = initial_text.replace(
            "      labels:\n", "      labels:\n        app: %s\n" % service)
        emit("        app: %s" % service not in initial_text
             and fixed_text == expected_fixed and fixed_text != initial_text,
             "svc-selector: repair adds only the missing Service-selector pod label")
        emit(all(command in instruction for command in
                 ("kubectl get svc", "kubectl get endpoints", "kubectl get events",
                  "kubectl describe pod", "kubectl get deploy", "curl -v")),
             "svc-selector: instruction teaches the Service triage checklist")
        emit(service not in instruction and mismatch not in instruction
             and service not in hint_text and mismatch not in hint_text,
             "svc-selector: instruction and hints do not reveal answers")

    if sid == "resource-limits":
        limit_checks = dumped
        pod = limit_checks[0]["answer"]
        oom_fragment = limit_checks[1]["answer"]
        checked_path = limit_checks[2]["path"]
        fixed_text = limit_checks[2]["answer"]
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        pods_text = fixture("kubectl_get_pods.txt")
        describe = fixture("kubectl_describe_pod.txt")
        events = fixture("kubectl_get_events.txt")
        logs = fixture("kubectl_logs.txt")
        pod_row = next((line for line in pods_text.splitlines()
                        if re.match(r"^" + re.escape(pod) + r"\s", line)), "")
        memory_match = re.search(r"^              memory: (\S+)$", fixed_text, re.MULTILINE)
        memory_limit = memory_match.group(1) if memory_match else ""
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "resource-limits: two hash checks precede the file check")
        emit(bool(pod_row) and "CrashLoopBackOff" in pod_row and pod in describe,
             "resource-limits: pod table/description identify the checked pod")
        emit("QoS Class:        BestEffort" in describe
             and "Limits:        <none>" in describe and "OOMKilled" in describe,
             "resource-limits: description shows BestEffort, no limits and OOMKilled")
        emit(oom_fragment in events and oom_fragment in describe and oom_fragment in logs
             and "OOMKilling" in events,
             "resource-limits: OOM fragment is shared by events, description and logs")
        emit(bool(memory_limit) and memory_limit in events and memory_limit in describe
             and memory_limit in logs,
             "resource-limits: fixtures reveal the exact repaired memory limit")
        image_line = "          image: registry.example/batch-worker:3.4.1"
        expected_lines = initial_text.splitlines()
        image_index = expected_lines.index(image_line)
        expected_lines[image_index + 1:image_index + 1] = [
            "          resources:", "            limits:",
            "              memory: %s" % memory_limit,
        ]
        emit("resources:" not in initial_text
             and fixed_text == "\n".join(expected_lines) + "\n",
             "resource-limits: repair inserts only the missing resource block")
        emit(all(command in instruction for command in
                 ("kubectl get pods", "kubectl describe pod", "kubectl get events",
                  "kubectl logs", "kubectl get deploy")),
             "resource-limits: instruction teaches the Kubernetes resource checklist")
        emit(pod not in instruction and oom_fragment not in instruction
             and (not memory_limit or memory_limit not in instruction)
             and pod not in hint_text and oom_fragment not in hint_text
             and (not memory_limit or memory_limit not in hint_text),
             "resource-limits: instruction and hints do not reveal answers")

    if sid == "tls-expiry":
        tls_checks = dumped
        certificate_answer = tls_checks[0]["answer"]
        current_path = tls_checks[1]["answer"]
        checked_path = tls_checks[2]["path"]
        fixed_text = tls_checks[2]["answer"]
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        initial_match = re.search(r"^certificate_file = (.+)$", initial_text, re.MULTILINE)
        fixed_match = re.search(r"^certificate_file = (.+)$", fixed_text, re.MULTILINE)
        expired_path = initial_match.group(1) if initial_match else ""
        fixed_path = fixed_match.group(1) if fixed_match else ""
        answer_parts = certificate_answer.split(":", 2)
        cn = answer_parts[0] if len(answer_parts) == 3 else ""
        expiry_date = answer_parts[1] if len(answer_parts) == 3 else ""
        expiry_fragment = answer_parts[2] if len(answer_parts) == 3 else ""
        curl_v = fixture("curl_v.txt")
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "tls-expiry: two hash checks precede the file check")
        emit(re.fullmatch(r"/etc/[^/]+/tls\.conf", checked_path) is not None,
             "tls-expiry: checked config follows /etc/<svc>/tls.conf")
        emit(bool(cn) and re.fullmatch(r"202[45]-\d{2}-\d{2}", expiry_date) is not None
             and ("subject: CN=" + cn) in curl_v and ("expire date: " + expiry_date) in curl_v
             and ("certificate expiry fragment: " + expiry_fragment) in curl_v,
             "tls-expiry: curl -v carries the checked CN, expiry date and fragment")
        emit("SSL certificate verify failed" in curl_v
             and "certificate has expired" in curl_v,
             "tls-expiry: verbose and head requests show certificate verification failure")
        emit(bool(expired_path) and expired_path != current_path
             and current_path == fixed_path and current_path in curl_v
             and fixed_text == initial_text.replace(expired_path, current_path),
             "tls-expiry: repair changes only the expired path to the curl reference")
        dig_text = fixture("dig.txt")
        nc_text = fixture("nc_zv.txt")
        answer_row = next((line for line in dig_text.splitlines()
                           if line.startswith(cn + ". ") and " IN A " in line), "")
        emit(bool(answer_row) and cn in fixture("curl_I.txt"),
             "tls-expiry: DNS resolves the certificate host and curl -I names it")
        own_nc = next((line for line in nc_text.splitlines()
                       if line.startswith("Connection to " + cn + " ") and "succeeded" in line), "")
        emit(bool(own_nc), "tls-expiry: nc shows the certificate host port open")
        emit(all(command in instruction for command in
                  ("curl -v", "curl -I", "dig", "nc -zv")),
             "tls-expiry: instruction teaches the TLS triage checklist")
        emit(certificate_answer not in instruction and current_path not in instruction
             and certificate_answer not in hint_text and current_path not in hint_text,
             "tls-expiry: instruction and hints do not reveal answers")

    if sid == "dependency-down":
        dep_checks = dumped
        dependency_answer = dep_checks[0]["answer"]
        correct_port = dep_checks[1]["answer"]
        checked_path = dep_checks[2]["path"]
        fixed_text = dep_checks[2]["answer"]
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        answer_parts = dependency_answer.split(":", 1)
        dependency_unit = answer_parts[0] if len(answer_parts) == 2 else ""
        incident = answer_parts[1] if len(answer_parts) == 2 else ""
        wrong_host_match = re.search(r"^dependency_host = (.+)$", initial_text, re.MULTILINE)
        correct_host_match = re.search(r"^dependency_host = (.+)$", fixed_text, re.MULTILINE)
        wrong_port_match = re.search(r"^dependency_port = (\d+)$", initial_text, re.MULTILINE)
        fixed_port_match = re.search(r"^dependency_port = (\d+)$", fixed_text, re.MULTILINE)
        wrong_host = wrong_host_match.group(1) if wrong_host_match else ""
        correct_host = correct_host_match.group(1) if correct_host_match else ""
        wrong_port = wrong_port_match.group(1) if wrong_port_match else ""
        fixed_port = fixed_port_match.group(1) if fixed_port_match else ""
        status = fixture("systemctl_status.txt")
        journal = fixture("journalctl_unit.txt")
        ss_text = fixture("ss_tlnp.txt")
        curl_text = fixture("curl_v.txt")
        ping_text = fixture("ping.txt")
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "dependency-down: two hash checks precede the file check")
        emit(re.fullmatch(r"/etc/([^/]+)/\1\.conf", checked_path) is not None,
             "dependency-down: checked config follows /etc/<svc>/<svc>.conf")
        emit(bool(dependency_unit) and "Active: failed" in status
             and dependency_unit in status and incident in status and incident in journal
             and dependency_unit in journal,
             "dependency-down: status/journal share the failed unit and incident fragment")
        emit("Connection refused" in journal and wrong_host in journal
             and wrong_port in journal and correct_host in journal and correct_port in journal,
             "dependency-down: journal contrasts the refused and approved endpoints")
        emit(bool(correct_port) and correct_port == fixed_port and correct_port != wrong_port
             and correct_port in ss_text and wrong_port not in ss_text,
             "dependency-down: ss listens on the checked port but not the configured port")
        emit("502 Bad Gateway" in curl_text and dependency_unit in curl_text
             and wrong_host in curl_text and wrong_port in curl_text,
             "dependency-down: curl shows the app's 502 and downstream target")
        emit(correct_host in ping_text and "0% packet loss" in ping_text,
             "dependency-down: ping confirms dependency host reachability")
        expected_fixed = initial_text.replace("dependency_host = " + wrong_host,
                                               "dependency_host = " + correct_host).replace(
            "dependency_port = " + wrong_port, "dependency_port = " + correct_port)
        emit(bool(wrong_host) and wrong_host != correct_host
             and fixed_text == expected_fixed and fixed_text != initial_text,
             "dependency-down: repair changes only dependency host and port")
        emit(all(command in instruction for command in
                  ("systemctl status", "journalctl -u", "curl -v", "ss -tlnp")),
             "dependency-down: instruction teaches the dependency triage checklist")
        emit(dependency_answer not in instruction and correct_host not in instruction
             and ("dependency_port = " + correct_port) not in instruction
             and dependency_answer not in hint_text and correct_host not in hint_text
             and ("dependency_port = " + correct_port) not in hint_text,
             "dependency-down: instruction and hints do not reveal answers")

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
            inspected = json.loads(fixture(engine + "_inspect.txt"))
            inspect = next((obj for obj in inspected
                            if obj.get("Name", "").lstrip("/") == name), {})
            states.append(inspect.get("State", {}))
            envs.append(inspect.get("Config", {}).get("Env", []))
            emit(name in ps_text and ("Exited (%s)" % exit_code) in ps_text,
                 "container-crash: %s ps fixture has the checked name and exit" % engine)
            emit(missing_var in logs_text,
                 "container-crash: %s logs fixture names the missing variable" % engine)
            emit(inspect.get("Name", "").lstrip("/") == name
                 and str(inspect["State"].get("ExitCode")) == exit_code
                 and missing_var not in inspect["Config"]["Env"],
                 "container-crash: %s inspect fixture confirms exit and missing env" % engine)
        if "image-pull" in stage_lines:
            log_marker = "# container %s (container-crash)\n" % name
            docker_report = fixture("docker_logs.txt").split(log_marker, 1)[-1]
            podman_report = fixture("podman_logs.txt").split(log_marker, 1)[-1]
            docker_report = docker_report.split("\n\n# container ", 1)[0]
            podman_report = podman_report.split("\n\n# container ", 1)[0]
            emit(bool(log_marker in fixture("docker_logs.txt"))
                 and docker_report == podman_report,
                 "container-crash: shared Docker/Podman log report still agrees with image-pull")
        else:
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
        if "selinux-avc" in stage_lines:
            mode = labelled_report("getenforce.txt", "getenforce (selinux-avc)")
            emit(mode == "Enforcing\n" and "avc: denied" in fixture("ausearch_avc.txt"),
                 "nginx-config: shared SELinux fixtures preserve the selected AVC denial")
        elif "perm-denied" in stage_lines:
            mode = labelled_report("getenforce.txt", "getenforce (perm-denied)")
            avc = labelled_report("ausearch_avc.txt", "ausearch -m avc (perm-denied)")
            emit(mode == "Permissive\n" and avc == "----\n<no matches>\n",
                 "nginx-config: shared permission fixtures preserve permissive/no-AVC evidence")
        else:
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

    if sid == "selinux-avc":
        fragment = dumped[0]["answer"]
        context = dumped[1]["answer"]
        checked_path = dumped[2]["path"]
        fixed_text = dumped[2]["answer"]
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        source_match = re.search(r"source_path = ([^\n]+)", initial_text)
        denied_path = source_match.group(1) if source_match else ""
        avc = fixture("ausearch_avc.txt")
        denials = [line for line in avc.splitlines() if "avc: denied" in line]
        wrong_contexts = re.findall(r"tcontext=([^\s]+)", "\n".join(denials))
        grant_match = re.search(r'avc: granted .*?path="([^"]+)"', avc)
        allowed_path = grant_match.group(1) if grant_match else ""
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "selinux-avc: two hash checks precede the file check")
        emit(re.fullmatch(r"/etc/([^/]+)/\1\.conf", checked_path) is not None,
             "selinux-avc: checked config follows /etc/<svc>/<svc>.conf")
        mode = labelled_report("getenforce.txt", "getenforce (selinux-avc)")
        emit(mode == "Enforcing\n" and len(denials) >= 2,
             "selinux-avc: enforcing mode and repeated AVC denials are present")
        emit(bool(denied_path) and denied_path in avc
             and all(fragment in line and denied_path in line for line in denials),
             "selinux-avc: denial lines carry the checked fragment and random path")
        emit(bool(wrong_contexts) and all(value != context for value in wrong_contexts)
             and context in avc,
             "selinux-avc: denial has a wrong context and the clue names the correct one")
        emit(bool(allowed_path) and allowed_path != denied_path
             and allowed_path not in initial_text
             and fixed_text == initial_text.replace(denied_path, allowed_path),
             "selinux-avc: repair changes only the denied source path")
        reference_match = re.search(r"(/etc/[^/\s`]+/references)", instruction)
        emit(reference_match is not None,
             "selinux-avc: instruction names a reference directory without its label answer")
        if reference_match:
            reference_dir = os.path.join(out, "fs", reference_match.group(1).lstrip("/"))
            references = os.listdir(reference_dir) if os.path.isdir(reference_dir) else []
            emit(context + ".conf" in references,
                 "selinux-avc: real reference filename carries the canonical context")
            if context + ".conf" in references:
                reference_path = os.path.join(reference_dir, context + ".conf")
                with open(reference_path, encoding="utf-8") as fh:
                    reference_text = fh.read()
                emit(context in reference_text and "owner = root" in reference_text
                     and allowed_path in reference_text
                     and (os.stat(reference_path).st_mode & 0o777) == 0o644,
                     "selinux-avc: reference has context, owner, allowed path and readable mode")
        emit(all(command in instruction for command in
                  ("getenforce", "ausearch -m avc", "ls -l")),
             "selinux-avc: instruction teaches getenforce, ausearch and real ls -l")
        emit(fragment not in instruction and context not in instruction
             and fragment not in hint_text and context not in hint_text,
             "selinux-avc: instruction and hints do not reveal hash answers")

    if sid == "port-conflict":
        holder = dumped[0]["answer"]
        held_port = dumped[1]["answer"]
        checked_path = dumped[2]["path"]
        fixed_text = dumped[2]["answer"]
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        initial_port = re.search(r"bind_port = (\d+)", initial_text)
        free_port = re.search(r"bind_port = (\d+)", fixed_text)
        held_value = initial_port.group(1) if initial_port else ""
        free_value = free_port.group(1) if free_port else ""
        ps_text = fixture("ps_aux.txt")
        ss_text = fixture("ss_tlnp.txt")
        curl_text = fixture("curl_v.txt")
        holder_row = next((line for line in ps_text.splitlines()
                           if re.search(r"\s" + re.escape(holder) + r"\s", line)), "")
        pid_match = re.match(r"\S+\s+(\d+)\s+", holder_row)
        connection_match = re.search(r"connection (conn-[0-9a-f]+): refused", curl_text)
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "port-conflict: two hash checks precede the file check")
        emit(re.fullmatch(r"/etc/([^/]+)/\1\.conf", checked_path) is not None,
             "port-conflict: checked config follows /etc/<svc>/<svc>.conf")
        emit(pid_match is not None and pid_match.group(1) in ss_text
             and holder in ss_text and held_port in ss_text and held_value == held_port,
             "port-conflict: ps PID/name and ss port identify the same holder")
        emit(bool(free_value) and free_value != held_value and free_value in ps_text
             and free_value not in ss_text
             and fixed_text == initial_text.replace("bind_port = " + held_value,
                                                     "bind_port = " + free_value),
             "port-conflict: repair uses the revealed free port and changes only bind_port")
        emit("localhost:" + held_port in curl_text
             and "Connection refused" in curl_text and connection_match is not None,
             "port-conflict: localhost curl failure has the conflicting port and fragment")
        emit(all(command in instruction for command in
                  ("ps aux | grep", "ss -tlnp", "curl -v localhost:")),
             "port-conflict: instruction teaches ps, ss and curl localhost triage")
        emit(holder not in instruction and held_port not in instruction
             and (not free_value or free_value not in instruction)
             and holder not in hint_text and held_port not in hint_text
             and (not free_value or free_value not in hint_text),
             "port-conflict: instruction and hints do not reveal answers")

    if sid == "blocked-port":
        blocked_checks = dumped
        target_host = blocked_checks[0]["answer"]
        failure_fragment = blocked_checks[1]["answer"]
        checked_path = blocked_checks[2]["path"]
        fixed_text = blocked_checks[2]["answer"]
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        host_match = re.search(r"^target_host = (.+)$", initial_text, re.MULTILINE)
        blocked_match = re.search(r"^bind_port = (\d+)$", initial_text, re.MULTILINE)
        allowed_match = re.search(r"^bind_port = (\d+)$", fixed_text, re.MULTILINE)
        timeout_match = re.search(r"^connect_timeout_ms = (\d+)$", initial_text, re.MULTILINE)
        service_match = re.search(r"^service_name = (.+)$", initial_text, re.MULTILINE)
        configured_host = host_match.group(1) if host_match else ""
        blocked_port = blocked_match.group(1) if blocked_match else ""
        allowed_port = allowed_match.group(1) if allowed_match else ""
        timeout_ms = timeout_match.group(1) if timeout_match else ""
        service_process = service_match.group(1) + "-listener" if service_match else ""
        nc_text = fixture("nc_zv.txt")
        ss_text = fixture("ss_tlnp.txt")
        curl_text = fixture("curl_v.txt")
        ping_text = fixture("ping.txt")
        dig_text = fixture("dig.txt")
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "blocked-port: two hash checks precede the file check")
        emit(re.fullmatch(r"/etc/([^/]+)/\1\.conf", checked_path) is not None
             and configured_host == target_host,
             "blocked-port: checked config follows /etc/<svc>/<svc>.conf and names the host")
        emit(bool(blocked_port) and bool(allowed_port) and blocked_port != allowed_port
             and fixed_text == initial_text.replace("bind_port = " + blocked_port,
                                                     "bind_port = " + allowed_port),
             "blocked-port: repair changes only the blocked port to the allowed listener")
        emit(target_host in nc_text and blocked_port in nc_text
             and failure_fragment in nc_text and "Connection timed out" in nc_text,
             "blocked-port: nc shows the target, blocked port, timeout and fragment")
        emit(target_host in curl_text and blocked_port in curl_text
             and failure_fragment in curl_text and "Connection timed out" in curl_text
             and bool(timeout_ms) and ("after " + timeout_ms + " milliseconds") in curl_text,
             "blocked-port: curl corroborates the connect timeout and fragment")
        emit(bool(service_process) and service_process in ss_text
             and (":" + allowed_port) in ss_text and (":" + blocked_port) not in ss_text,
             "blocked-port: ss shows the service listener but not the configured port")
        emit(target_host in ping_text and "0% packet loss" in ping_text
             and target_host in dig_text and " IN A " in dig_text,
             "blocked-port: ping/DNS establish host reachability separately from the port")
        emit(all(command in instruction for command in
                  ("nc -zv", "ss -tlnp", "curl -v", "ping")),
             "blocked-port: instruction teaches the network triage checklist")
        emit(target_host not in instruction and failure_fragment not in instruction
             and ("bind_port = " + allowed_port) not in instruction
             and target_host not in hint_text and failure_fragment not in hint_text
             and ("bind_port = " + allowed_port) not in hint_text,
             "blocked-port: instruction and hints do not reveal answers")

    if sid == "perm-denied":
        perm_checks = dumped
        denied_answer = perm_checks[0]["answer"]
        owner = perm_checks[1]["answer"]
        checked_path = perm_checks[2]["path"]
        fixed_text = perm_checks[2]["answer"]
        denied_path, _, fragment = denied_answer.partition(":")
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        denied_match = re.search(r"^data_path = (.+)$", initial_text, re.MULTILINE)
        reference_match = re.search(r"^data_path = (.+)$", fixed_text, re.MULTILINE)
        configured_path = denied_match.group(1) if denied_match else ""
        reference_path = reference_match.group(1) if reference_match else ""
        unit = os.path.basename(checked_path)[:-5] + ".service"
        status = fixture("systemctl_status.txt")
        journal = fixture("journalctl_unit.txt")
        denial_lines = [line for line in journal.splitlines() if "Permission denied" in line]
        source_lines = [re.search(re.escape(denied_path) + r":(\d+)", line)
                        for line in denial_lines]
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "perm-denied: two hash checks precede the file check")
        emit(re.fullmatch(r"/etc/([^/]+)/\1\.conf", checked_path) is not None
             and configured_path == denied_path,
             "perm-denied: checked config follows /etc/<svc>/<svc>.conf and names the denied path")
        emit(bool(reference_path) and reference_path != denied_path
             and fixed_text == initial_text.replace("data_path = " + denied_path,
                                                     "data_path = " + reference_path),
             "perm-denied: repair changes only data_path to the readable reference")
        emit(len(denial_lines) >= 2 and all(denied_path in line and fragment in line
                                            for line in denial_lines),
             "perm-denied: journal repeats Permission denied with the checked path/fragment")
        denied_file = os.path.join(out, "fs", denied_path.lstrip("/"))
        with open(denied_file, encoding="utf-8") as fh:
            denied_data = fh.read()
        emit(all(match is not None and 2 <= int(match.group(1)) <= len(denied_data.splitlines())
                 for match in source_lines),
             "perm-denied: randomized journal source line exists in the denied file")
        emit("Active: failed" in status and unit in status and fragment in status
             and unit in journal,
             "perm-denied: unit status and journal identify the same failure")
        mode = labelled_report("getenforce.txt", "getenforce (perm-denied)")
        avc = labelled_report("ausearch_avc.txt", "ausearch -m avc (perm-denied)")
        emit(mode == "Permissive\n" and avc == "----\n<no matches>\n",
             "perm-denied: permissive mode and no AVC rule out SELinux")
        reference_file = os.path.join(out, "fs", reference_path.lstrip("/"))
        with open(reference_file, encoding="utf-8") as fh:
            reference_text = fh.read()
        emit(owner == "nginx" and reference_path.startswith("/srv/sre-trainer/references/")
             and (os.stat(reference_file).st_mode & 0o777) == 0o644
             and (os.stat(denied_file).st_mode & 0o777) == 0o600
             and "owner" not in reference_text.lower(),
             "perm-denied: reference is readable and relies on real ownership metadata")
        emit(all(command in instruction for command in
                  ("systemctl status", "journalctl -u", "ls -l", "stat",
                   "getenforce", "ausearch -m avc")),
             "perm-denied: instruction teaches the permission triage checklist")
        emit(denied_path not in instruction and fragment not in instruction
             and owner not in instruction and denied_path not in hint_text
             and fragment not in hint_text and owner not in hint_text,
             "perm-denied: instruction and hints do not reveal answers")

    if sid == "oom-kill":
        killed_answer = dumped[0]["answer"]
        available = dumped[1]["answer"]
        checked_path = dumped[2]["path"]
        fixed_text = dumped[2]["answer"]
        killed_pid, process = killed_answer.split(":", 1)
        journal = fixture("journalctl_unit.txt")
        oom_matches = re.findall(
            r"Out of memory: Killed process (\d+) \(([^)]+)\)", journal)
        safe_matches = re.findall(r"safe memory_limit_mb=(\d+)", journal)
        free_report = snapshot("free_m.txt", "oom-kill")
        top_report = snapshot("top.txt", "oom-kill")
        mem_line = next((line for line in free_report.splitlines()
                         if line.startswith("Mem:")), "")
        mem_fields = mem_line.split()
        shown_available = mem_fields[-1] if mem_fields else ""
        process_row = next((line for line in top_report.splitlines()
                            if process in line), "")
        row_fields = process_row.split()
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        initial_lines = initial_text.splitlines()
        fixed_lines = fixed_text.splitlines()
        changed = [(a, b) for a, b in zip(initial_lines, fixed_lines) if a != b]
        safe_line = "memory_limit_mb = " + (safe_matches[0] if safe_matches else "")
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "oom-kill: two hash checks precede the file check")
        emit(re.fullmatch(r"/etc/([^/]+)/\1\.conf", checked_path) is not None,
             "oom-kill: checked config follows /etc/<svc>/<svc>.conf")
        emit(len(oom_matches) == 1 and oom_matches[0] == (killed_pid, process),
             "oom-kill: journal has one checked OOM PID/process line")
        emit(len(safe_matches) == 1 and shown_available == available,
             "oom-kill: journal safe-limit clue and free available figure are unambiguous")
        emit(len(row_fields) >= 12 and row_fields[0] == killed_pid
             and row_fields[-1] == process and int(row_fields[5]) > int(available),
             "oom-kill: top corroborates the killed high-MEM process")
        if any(line.startswith("memory_limit_mb = ") for line in initial_lines):
            expected_fixed = initial_text.replace(
                next(line for line in initial_lines if line.startswith("memory_limit_mb = ")),
                safe_line)
            emit(len(initial_lines) == len(fixed_lines) and len(changed) == 1
                 and fixed_text == expected_fixed,
                 "oom-kill: repair replaces only the unsafe memory limit")
        else:
            expected_lines = list(initial_lines)
            service_index = next((i for i, line in enumerate(expected_lines)
                                  if line.startswith("service_name = ")), -1)
            if service_index >= 0:
                expected_lines.insert(service_index + 1, safe_line)
            emit(service_index >= 0 and fixed_text == "\n".join(expected_lines) + "\n",
                 "oom-kill: repair inserts the missing limit after service_name")
        emit(all(fixture(name) != NEUTRAL
                 for name in ("journalctl_unit.txt", "journalctl_xe.txt",
                              "free_m.txt", "top.txt")),
             "oom-kill: journal and resource fixtures contain scenario data")
        emit(all(command in instruction for command in
                  ("journalctl -u", "journalctl -xe", "free -m", "top")),
             "oom-kill: instruction teaches journal, free and top triage")
        emit(killed_answer not in instruction and process not in instruction
             and process not in hint_text and (not safe_matches or safe_matches[0] not in hint_text),
             "oom-kill: instruction and hints do not reveal answers")

    if sid == "image-pull":
        correct_image = dumped[0]["answer"]
        reason_fragment = dumped[1]["answer"]
        checked_path = dumped[2]["path"]
        fixed_text = dumped[2]["answer"]
        with open(os.path.join(out, "fs", checked_path.lstrip("/")),
                  encoding="utf-8") as fh:
            initial_text = fh.read()
        image_match = re.search(r"^IMAGE=(.+)$", initial_text, re.MULTILINE)
        wrong_image = image_match.group(1) if image_match else ""
        correct_tag = correct_image.rsplit(":", 1)[-1]
        emit([c["type"] for c in dumped] == ["hash", "hash", "file"],
             "image-pull: two hash checks precede the file check")
        emit(bool(wrong_image) and wrong_image != correct_image
             and fixed_text == initial_text.replace("IMAGE=" + wrong_image,
                                                    "IMAGE=" + correct_image),
             "image-pull: repair changes only IMAGE to the available release")
        for engine in ("docker", "podman"):
            ps_text = fixture(engine + "_ps_a.txt")
            logs_text = fixture(engine + "_logs.txt")
            inspected = json.loads(fixture(engine + "_inspect.txt"))
            container_name = re.search(r"/home/trainee/containers/([^/]+)\.env",
                                       checked_path)
            name = container_name.group(1) if container_name else ""
            inspect = next((obj for obj in inspected
                            if obj.get("Name", "").lstrip("/") == name), {})
            emit(bool(name) and name in ps_text and wrong_image in ps_text
                 and "Created" in ps_text,
                 "image-pull: %s ps fixture shows the failed container" % engine)
            emit(wrong_image in logs_text and correct_image in logs_text
                 and reason_fragment in logs_text and "manifest unknown" in logs_text
                 and "ImagePullBackOff" in logs_text,
                 "image-pull: %s logs carry wrong/correct tags and failure fragment" % engine)
            emit(inspect.get("Labels", {}).get("registry-available-tag") == correct_tag
                 and reason_fragment in inspect.get("State", {}).get("Error", ""),
                 "image-pull: %s inspect confirms release tag and failure" % engine)
        emit(all(command in instruction for command in
                  ("docker ps -a", "podman ps -a", "docker logs", "podman logs",
                   "docker inspect", "podman inspect")),
             "image-pull: instruction teaches Docker and Podman triage")
        emit(correct_image not in instruction and reason_fragment not in instruction
             and correct_image not in hint_text and reason_fragment not in hint_text,
             "image-pull: instruction and hints do not reveal answers")

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

    run_generator_case "default sixteen-template troubleshoot generation" default 16
    run_generator_case "original two-template PICK" pilot-pick 2 \
        svc-crashloop,k8s-crashloop
    run_generator_case "repeat generation for determinism" pilot-repeat 2 \
        svc-crashloop,k8s-crashloop
    if cmp -s "$WORK/gen-pilot-pick/valid_names.inc" \
            "$WORK/gen-pilot-repeat/valid_names.inc"; then
        ok "valid_names.inc is deterministic for the same seed and PICK"
    else
        bad "valid_names.inc differs across identical generation runs"
        diff -u "$WORK/gen-pilot-pick/valid_names.inc" \
            "$WORK/gen-pilot-repeat/valid_names.inc" | sed 's/^/        | /'
    fi
    if diff -r "$WORK/gen-pilot-pick/fixtures" "$WORK/gen-pilot-repeat/fixtures" \
            >"$WORK/pilot-repeat-fixture-diff.log" 2>&1; then
        ok "repeat generation keeps all 32 fixture bytes identical"
    else
        bad "repeat generation changed fixture bytes"
        sed 's/^/        | /' "$WORK/pilot-repeat-fixture-diff.log"
    fi

    # Compile against the generator's real include and exercise names that do
    # not exist in the fixed host-smoke table above.
    PILOT_BIN_DIR=$WORK/pilot-bin
    mkdir -p "$PILOT_BIN_DIR"
    if gcc -O2 -static -Wall -Wextra -Werror -DSREMOCK_VALID_NAMES=1 \
            -I"$WORK/gen-pilot-pick" -o "$WORK/sremock-pilot" \
            "$REPO_DIR/src/sremock.c" >"$WORK/pilot-build.log" 2>&1; then
        ok "sremock compiles against generated valid_names.inc"
        for command in $MOCK_COMMANDS; do
            ln -s "$WORK/sremock-pilot" "$PILOT_BIN_DIR/$command"
        done
        pilot_unit=$(awk -F'"' '$2 == "unit" { print $4; exit }' \
            "$WORK/gen-pilot-pick/valid_names.inc")
        pilot_pod=$(awk -F'"' '$2 == "pod" { print $4; exit }' \
            "$WORK/gen-pilot-pick/valid_names.inc")
        pilot_cm=$(awk -F'"' '$2 == "cm" { print $4; exit }' \
            "$WORK/gen-pilot-pick/valid_names.inc")
        pilot_deploy=$(awk -F'"' '$2 == "deploy" { print $4; exit }' \
            "$WORK/gen-pilot-pick/valid_names.inc")
        expect_fixture "generated unit name is accepted with its suffix" \
            systemctl_status.txt "$PILOT_BIN_DIR/systemctl" status "$pilot_unit"
        expect_fixture "generated unit name is accepted without its suffix" \
            systemctl_status.txt "$PILOT_BIN_DIR/systemctl" status "${pilot_unit%.service}"
        expect_fixture "generated pod TYPE/NAME is accepted" kubectl_get_pods.txt \
            "$PILOT_BIN_DIR/kubectl" get "po/$pilot_pod"
        expect_fixture "generated configmap shorthand is accepted" kubectl_get_configmap.txt \
            "$PILOT_BIN_DIR/kubectl" get cm "$pilot_cm"
        expect_fixture "generated deployment shorthand is accepted" kubectl_get_deploy.txt \
            "$PILOT_BIN_DIR/kubectl" get deploy "$pilot_deploy"
        expect_fixture "generated pod is accepted by describe" kubectl_describe_pod.txt \
            "$PILOT_BIN_DIR/kubectl" describe pods/"$pilot_pod"
        expect_fixture "generated pod is accepted by logs" kubectl_logs.txt \
            "$PILOT_BIN_DIR/kubectl" logs po/"$pilot_pod"
        expect_error "an invented pod name is rejected by the generated table" 1 \
            'Error from server (NotFound): pods "definitely-missing" not found' \
            "$PILOT_BIN_DIR/kubectl" get po definitely-missing
        other_seed=$((SEED + 1))
        if python3 "$GEN" --seed "$other_seed" --set 2 --profile troubleshoot \
                --pick svc-crashloop,k8s-crashloop --out "$WORK/gen-pilot-other" \
                >"$WORK/gen-pilot-other.log" 2>&1; then
            other_pod=$(awk -F'"' '$2 == "pod" { print $4; exit }' \
                "$WORK/gen-pilot-other/valid_names.inc")
            expect_error "a real pod name from another seed is rejected" 1 \
                "Error from server (NotFound): pods \"$other_pod\" not found" \
                "$PILOT_BIN_DIR/kubectl" get po "$other_pod"
            if cmp -s "$WORK/gen-pilot-pick/valid_names.inc" \
                    "$WORK/gen-pilot-other/valid_names.inc"; then
                bad "different seeds unexpectedly produced the same valid-name table"
            else
                ok "different seeds produce different compiled resource names"
            fi
        else
            bad "other-seed generation failed"
            sed 's/^/        | /' "$WORK/gen-pilot-other.log"
        fi
        expect_error "keys absent from selected templates stay absent" 1 \
            'Error: No such container: demo' "$PILOT_BIN_DIR/docker" logs demo
    else
        bad "sremock failed to compile against generated valid_names.inc"
        sed 's/^/        | /' "$WORK/pilot-build.log"
    fi

    # A reversed, non-pool order and a deliberately different SET prove PICK
    # controls both the exact subset order and the effective stage count.
    run_generator_case "new two-template PICK with reversed order" new-pick 1 \
        container-crash,disk-full
    run_generator_case "wave-three two-template PICK" wave3-pick 1 \
        nginx-config,dns-mismatch
    run_generator_case "wave-three PICK in reversed order" wave3-reversed 6 \
        dns-mismatch,nginx-config
    run_generator_case "wave-four two-template PICK" wave4-pick 1 \
        selinux-avc,port-conflict
    run_generator_case "wave-four PICK in reversed order" wave4-reversed 8 \
        port-conflict,selinux-avc
    run_generator_case "wave-five two-template PICK" wave5-pick 1 \
        oom-kill,image-pull
    run_generator_case "wave-five PICK in reversed order" wave5-reversed 10 \
        image-pull,oom-kill
    run_generator_case "wave-six two-template PICK" wave6-pick 1 \
        svc-selector,resource-limits
    run_generator_case "wave-six PICK in reversed order" wave6-reversed 12 \
        resource-limits,svc-selector
    run_generator_case "mixed Kubernetes PICK" wave6-mixed 1 \
        resource-limits,k8s-crashloop,svc-selector
    run_generator_case "mixed Kubernetes PICK reversed" wave6-mixed-reversed 12 \
        svc-selector,k8s-crashloop,resource-limits
    run_generator_case "wave-seven two-template PICK" wave7-pick 1 \
        tls-expiry,dependency-down
    run_generator_case "wave-seven PICK in reversed order" wave7-reversed 14 \
        dependency-down,tls-expiry
    run_generator_case "mixed shared-evidence PICK" wave7-mixed 1 \
        tls-expiry,dns-mismatch,dependency-down,svc-crashloop,port-conflict,oom-kill,svc-selector
    run_generator_case "mixed shared-evidence PICK reversed" wave7-mixed-reversed 14 \
        svc-selector,oom-kill,port-conflict,svc-crashloop,dependency-down,dns-mismatch,tls-expiry
    if diff -r "$WORK/gen-wave7-pick/fixtures" "$WORK/gen-wave7-reversed/fixtures" \
            >"$WORK/wave7-fixture-diff.log" 2>&1; then
        ok "wave-seven fixture bytes are independent of PICK order"
    else
        bad "wave-seven fixture bytes differ between forward and reversed PICK"
        sed 's/^/        | /' "$WORK/wave7-fixture-diff.log"
    fi
    if diff -r "$WORK/gen-wave7-mixed/fixtures" "$WORK/gen-wave7-mixed-reversed/fixtures" \
            >"$WORK/mixed-wave7-fixture-diff.log" 2>&1; then
        ok "mixed shared-evidence fixture bytes are independent of PICK order"
    else
        bad "mixed shared-evidence fixture bytes differ between forward and reversed PICK"
        sed 's/^/        | /' "$WORK/mixed-wave7-fixture-diff.log"
    fi
    run_generator_case "wave-eight two-template PICK" wave8-pick 1 \
        blocked-port,perm-denied
    run_generator_case "wave-eight PICK reversed with SET override" wave8-reversed 99 \
        perm-denied,blocked-port
    if diff -r "$WORK/gen-wave8-pick/fixtures" "$WORK/gen-wave8-reversed/fixtures" \
            >"$WORK/wave8-fixture-diff.log" 2>&1; then
        ok "wave-eight fixture bytes are independent of PICK order"
    else
        bad "wave-eight fixture bytes differ between forward and reversed PICK"
        sed 's/^/        | /' "$WORK/wave8-fixture-diff.log"
    fi
    if cmp -s "$WORK/gen-wave8-pick/valid_names.inc" \
            "$WORK/gen-wave8-reversed/valid_names.inc"; then
        ok "valid_names.inc is independent of PICK order"
    else
        bad "valid_names.inc differs between forward and reversed PICK"
    fi
    run_generator_case "mixed wave-eight shared-evidence PICK" wave8-mixed 1 \
        blocked-port,perm-denied,selinux-avc,nginx-config,dns-mismatch,dependency-down,port-conflict,svc-crashloop
    run_generator_case "mixed wave-eight shared-evidence PICK reversed" wave8-mixed-reversed 16 \
        svc-crashloop,port-conflict,dependency-down,dns-mismatch,nginx-config,selinux-avc,perm-denied,blocked-port
    if diff -r "$WORK/gen-wave8-mixed/fixtures" "$WORK/gen-wave8-mixed-reversed/fixtures" \
            >"$WORK/mixed-wave8-fixture-diff.log" 2>&1; then
        ok "mixed wave-eight fixture bytes are independent of PICK order"
    else
        bad "mixed wave-eight fixture bytes differ between forward and reversed PICK"
        sed 's/^/        | /' "$WORK/mixed-wave8-fixture-diff.log"
    fi
    if python3 "$GEN" --seed "$SEED" --set 17 --profile troubleshoot \
            --out "$WORK/gen-invalid-set" >"$WORK/gen-invalid-set.log" 2>&1; then
        bad "troubleshoot SET=17 must be rejected"
    elif grep -F -- "between 1 and 16" "$WORK/gen-invalid-set.log" >/dev/null 2>&1 \
            && [ ! -e "$WORK/gen-invalid-set" ]; then
        ok "troubleshoot SET=17 is rejected early with the 1..16 range"
    else
        bad "troubleshoot SET=17 rejection lacks the range or creates output"
        sed 's/^/        | /' "$WORK/gen-invalid-set.log"
    fi
    if diff -r "$WORK/gen-wave6-pick/fixtures" "$WORK/gen-wave6-reversed/fixtures" \
            >"$WORK/wave6-fixture-diff.log" 2>&1; then
        ok "wave-six fixture bytes are independent of PICK order"
    else
        bad "wave-six fixture bytes differ between forward and reversed PICK"
        sed 's/^/        | /' "$WORK/wave6-fixture-diff.log"
    fi
    if diff -r "$WORK/gen-wave6-mixed/fixtures" "$WORK/gen-wave6-mixed-reversed/fixtures" \
            >"$WORK/mixed-fixture-diff.log" 2>&1; then
        ok "mixed Kubernetes fixture bytes are independent of PICK order"
    else
        bad "mixed Kubernetes fixture bytes differ between forward and reversed PICK"
        sed 's/^/        | /' "$WORK/mixed-fixture-diff.log"
    fi
fi

# ---------------------------------------------------------------- summary
printf '\n== summary ==\n'
printf 'passed: %d   failed: %d   skipped: %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ] || exit 1
printf 'OK\n'
