# Day 2: the troubleshoot profile

The trainer's second profile is a pool of sixteen diagnostic drills whose terminal
commands are answered from fixtures instead of a live machine. It exists to
practise the read-then-repair loop — inspect the evidence, decide on one change,
make it, prove it — without shipping a privileged container, a systemd host or a
Kubernetes cluster.

Everything outside the drills is unchanged: the same sshd/entrypoint image
layout, the same setuid `verify`, the same `/var/lib/sre-trainer/state/`
progress and the same flag, timing and record flow. If you have used the default
profile, the interaction is identical; only the stages differ.

- Profile flag: `PROFILE=troubleshoot`
- Templates: `svc-crashloop`, `k8s-crashloop`, `disk-full`, `container-crash`,
  `nginx-config`, `dns-mismatch`, `selinux-avc`, `port-conflict`, `oom-kill`,
  `image-pull`, `svc-selector`, `resource-limits`, `tls-expiry`, `dependency-down`,
  `blocked-port`, `perm-denied`
- Image / container: `sre-terminal-trainer-troubleshoot:local` / `sre-trainer-troubleshoot`
  (separate from the default names, so both profiles can run side by side)

## The sixteen templates

Each stage's brief names the exact files and check ids. The checklists below are
the commands the stage is built around; the facts they expose are randomized per
build, so read them rather than memorising anything.

### `svc-crashloop`

A service unit is failing on this machine. The evidence is split between the
unit state, the journal and the listening sockets:

| Command | What it is for |
|---|---|
| `systemctl status <unit>` | Loaded path, active state, exit status of the main process |
| `journalctl -u <unit> -n 100 --no-pager` | The unit's recent history: the repeated failure signature and two one-off clues |
| `journalctl -xe` | The same failure in the short boot-wide form |
| `ss -tlnp` | Listening ports with the owning process, including the port the service wants |

One check is a hash check over facts found in those outputs; one is a file check
on the service's configuration file; one is a hash check over the process name
reported by `ss`. The brief states the exact end state for the file: the shipped
file with only the settings it names changed, edited in place, byte for byte.
The values for those settings have to be recovered from the journal — do not
guess them.

### `k8s-crashloop`

A pod is not staying up in a namespace. The evidence is split across the
workload view, the pod description, the container logs, the event stream and the
ConfigMap:

| Command | What it is for |
|---|---|
| `kubectl get pods` | Pod name, status, restart count |
| `kubectl describe pod <pod>` | Container state, last termination, which config key the environment requests |
| `kubectl logs <pod>` | The container's own failure reason |
| `kubectl get events` | Back-off and configuration-related events with a reason code |
| `kubectl get configmap <name>` | The keys the ConfigMap actually defines |
| `kubectl get deploy <name>` | Deployment readiness (context only) |

One check is a hash check over the pod name and crash reason; one is a hash
check over the config key the container reports as missing; one is a file check
on the deployment manifest under `/home/trainee/k8s/`. The brief states the
exact one-line change it expects, and the value for that line comes from the
ConfigMap and event output.

### `disk-full`

A host has run out of disk space. Correlate filesystem, directory and load
evidence before making the repair:

| Command | What it is for |
|---|---|
| `df -h` | Filesystems and their use percentage, including the full mount |
| `du -sh /var/log/* \| sort -h` | Log directories ordered by size; the largest is the runaway directory |
| `free -m` / `top` | Memory and CPU pressure for context |

Two hash checks use facts from `df` and `du`; one file check repairs the
logrotate policy. The brief specifies the byte-exact change. Inspect the shipped
configuration before editing it in place.

### `container-crash`

A container exits immediately because required environment configuration is
missing. Both the **Docker and Podman mocks** are exercised, each with its own
process, log and inspection fixtures:

| Command | What it is for |
|---|---|
| `docker ps -a` / `podman ps -a` | Container name and exited status |
| `docker logs <name>` / `podman logs <name>` | Fatal error naming the missing environment variable |
| `docker inspect <name>` / `podman inspect <name>` | Exit code and the environment actually configured |

Two hash checks use the container state and the missing variable reported by
the logs; one file check repairs the environment file. Derive the variable from
the evidence rather than guessing it, then make the brief's exact in-place
change.

### `nginx-config`

An nginx virtual host does not pass its configuration test. Correlate the parse
error with real file metadata and a canonical reference asset:

| Command | What it is for |
|---|---|
| `nginx -t` | The unterminated directive, source path, line number and diagnostic token |
| `ls -l <reference-config>` | The service account that owns the reference asset |
| `cat <reference-config>` | The canonical `server_name` for the virtual host |
| `getenforce` / `ausearch -m avc` | Benign SELinux context; not the load-bearing failure |

Two hash checks use the syntax diagnostic and the owner name. The file check
repairs the shipped site config in place: terminate the reported directive and
replace only `server_name` with the reference value. The reference asset is owned
by the service account in the image, so its owner is real metadata rather than
text invented by a mock.

### `dns-mismatch`

An application endpoint returns a gateway failure after its hostname stops
matching the intended service. Separate the canonical service name from the
stale backend currently reached through DNS:

| Command | What it is for |
|---|---|
| `dig <name> A` / `nslookup <name>` | The queried service name and its current CNAME target |
| `curl -I <url>` | The gateway response from the stale backend |
| `curl -v <url>` | Connection details and the transport diagnostic token |
| `ping <host>` | Name/address reachability for context |
| `nc -zv <host> <port>` | The refused backend port and matching connection diagnostic |

Two hash checks use the canonical service hostname and the connection token. The
file check changes only the hostname in `/home/trainee/app/endpoint.conf`; the
scheme, port, path and every other line must stay byte-for-byte unchanged.

### `selinux-avc`

A service is blocked by mandatory access control. Confirm the enforcement mode,
then correlate the access-vector-control records with the shipped application
configuration and its reference directory:

| Command | What it is for |
|---|---|
| `getenforce` | Whether SELinux is enforcing policy |
| `ausearch -m avc` | Repeated denial records: process token, path and wrong context, plus the allowed-source clue |
| `ls -l <reference-dir>` | Real file ownership and the label-named canonical reference |
| `cat <reference-file>` | The canonical context and allowed path |

Two hash checks use the denial token and canonical SELinux context. The file
check changes only the denied `source_path` in `/etc/<svc>/<svc>.conf` to the
allowed path. The reference's owner is real image metadata; its label is carried
in the reference filename and contents, not by a live SELinux filesystem.

### `port-conflict`

A service cannot bind because another process already owns its configured port.
Correlate socket, process and local-connection evidence before editing:

| Command | What it is for |
|---|---|
| `ss -tlnp` | Listening addresses, ports and owning processes |
| `ps aux \| grep <candidate>` | The holder's PID and command |
| `ps aux` | The full process table, including the port planner's proposed free port |
| `curl -v localhost:<port>` | The local connection failure and diagnostic token |

Two hash checks use the holder's process name and conflicting port. The file
check changes only `bind_port` in `/etc/<svc>/<svc>.conf` to the proposed free
port, preserving every other byte.

### `oom-kill`

A service has been killed after memory exhaustion. Correlate the kernel's OOM
record with current memory figures and the process view before choosing a cap:

| Command | What it is for |
|---|---|
| `journalctl -u <unit> -n 100 --no-pager` | Killed PID/process and the memory-policy incident's approved limit |
| `journalctl -xe` | The same failure in the short boot-wide form |
| `free -m` | Total, used and available memory in MiB |
| `top` | The high-MEM process and its resident memory |

Two hash checks use the killed process identity and the available-memory
figure. The file check corrects only `memory_limit_mb` in
`/etc/<svc>/<svc>.conf`; the brief explains both the replacement and missing-
setting cases. The safe value comes from the journal evidence, not a guess.

### `image-pull`

A container exists but its configured image could not be pulled. Compare the
process list, pull diagnostics and inspection metadata using either supported
container tool:

| Command | What it is for |
|---|---|
| `docker ps -a` / `podman ps -a` | The created container and attempted image |
| `docker logs <name>` / `podman logs <name>` | Pull failure, request-id and available release image |
| `docker inspect <name>` / `podman inspect <name>` | Configured image, inspection error and release-tag clue |

Two hash checks use the available image reference and failure request-id. The
file check replaces only `IMAGE` in `/home/trainee/containers/<name>.env`,
preserving the rest of the deploy environment byte for byte.

### `svc-selector`

A Kubernetes Service has no ready endpoints and its cluster address refuses
connections. Compare the Service selector with the actual pod-template labels,
then correlate the endpoint controller, events, pod description and deployment:

| Command | What it is for |
|---|---|
| `kubectl get svc [name]` | Service name, cluster address, port and selector |
| `kubectl get endpoints [name]` | The missing endpoint set and controller mismatch fragment |
| `kubectl get events` | `EndpointsNotFound` and the matching mismatch fragment |
| `kubectl get pods` | Randomized pod names and labels, including non-matching candidates |
| `kubectl describe pod <pod>` | The affected pod's labels and Service-match context |
| `kubectl get deploy <name>` | Deployment readiness and workload identity |
| `curl -v http://<cluster-ip>:<port>/` | Refused connection to the Service address |

Two hash checks use the Service name and selector-mismatch fragment. The file
check repairs a deployment under `/home/trainee/k8s/` by adding only the
pod-template label named by the Service selector. Existing pod labels, the
Deployment selector, image, port and all other bytes must remain unchanged.

### `resource-limits`

A Kubernetes worker is crash-looping after an OOM kill. Establish the pod
identity, its `BestEffort` QoS, the absent limits and the reviewed memory value
before editing the workload:

| Command | What it is for |
|---|---|
| `kubectl get pods` | CrashLoopBackOff pod name, restart count and labels |
| `kubectl describe pod <pod>` | Last termination `OOMKilled`, `BestEffort` QoS, no limits, OOM fragment and memory-policy clue |
| `kubectl get events` | OOM event, incident fragment and reviewed memory limit |
| `kubectl logs <pod>` | Container-side corroboration of the OOM and recommended limit |
| `kubectl get deploy <name>` | Deployment readiness and workload identity |

Two hash checks use the pod name and OOM incident fragment. The file check
repairs a deployment under `/home/trainee/k8s/` by inserting only the missing
`resources.limits.memory` block after the container image, using the value
revealed by the fixtures. Every existing line must remain byte-for-byte
unchanged.

### `tls-expiry`

An HTTPS endpoint fails certificate validation. Separate DNS and transport health
from the certificate itself before touching the service configuration:

| Command | What it is for |
|---|---|
| `curl -v <https-url>` | Connection progress, certificate CN, validity dates, expiry fragment and the current-certificate reference |
| `curl -I <https-url>` | Corroborates that certificate verification fails before a response |
| `dig <name> A` | Confirms that the certificate hostname resolves |
| `nc -zv <host> 443` | Confirms that the TCP port is open despite the TLS failure |

One hash check uses the certificate identity and expiry evidence; another uses
the current certificate path revealed by the verbose report. The file check
repairs `/etc/<svc>/tls.conf` by changing only `certificate_file`. Every other
line, including the key path and listener, must remain byte-for-byte unchanged.

### `dependency-down`

An application returns a gateway failure because its downstream dependency
refuses the configured connection. Correlate unit, journal, socket and network
evidence:

| Command | What it is for |
|---|---|
| `systemctl status <dependency-unit>` | Failed dependency state, unit identity, incident fragment and endpoint environment |
| `journalctl -u <app-unit> -n 100 --no-pager` | The refused configured endpoint and the approved dependency endpoint |
| `curl -v <app-url>` | The application's 502 and downstream target |
| `ss -tlnp` | The dependency port actually listening, distinct from the configured port |
| `ping <dependency-host>` | Host reachability, which does not prove the service port is available |

One hash check uses the dependency unit and incident fragment; another uses the
listening dependency port corroborated by the endpoint clues. The file check
repairs `/etc/<svc>/<svc>.conf` by changing only `dependency_host` and
`dependency_port` to the approved endpoint, preserving every other byte.

### `blocked-port`

A service endpoint times out even though its target host is reachable. Separate
host reachability from TCP service availability before changing the service
configuration:

| Command | What it is for |
|---|---|
| `nc -zv <host> <port>` | Target hostname, configured port, timeout and connection diagnostic token |
| `curl -v <url>` | Verbose corroboration of the connect timeout and the same diagnostic token |
| `ss -tlnp` | The service's actual listening port, distinct from the unreachable configured port |
| `ping <host>` | Host reachability, which does not prove that a TCP port is available |
| `dig <host> A` | Optional name/address corroboration |

Two hash checks use the target hostname and connection token. The file check
changes only `bind_port` in `/etc/<svc>/<svc>.conf` to the service's listening
port revealed by the socket evidence. Every other byte stays unchanged.

### `perm-denied`

A service unit fails while opening its configured data file. Correlate the unit
and journal evidence with real filesystem metadata, and rule out mandatory
access control before repairing the path:

| Command | What it is for |
|---|---|
| `systemctl status <unit>` | Failed unit, configured path and incident context |
| `journalctl -u <unit> -n 100 --no-pager` | `Permission denied`, full path, randomized source line and incident fragment |
| `ls -l <path>` | Real owner/group names and mode bits for the denied and reference files |
| `stat <path>` | Numeric ownership and permission metadata |
| `getenforce` | Permissive SELinux mode for this scenario |
| `ausearch -m avc` | No matching AVC records, ruling out an SELinux denial |

Two hash checks use the denied path plus incident fragment and the reference
file's owner name. The file check changes only `data_path` in
`/etc/<svc>/<svc>.conf` to the readable reference file. The reference's ownership
is real image metadata, not text in its contents.

## How the mocks work

Orchestration detail, not something a trainee needs to act on.

- **One binary, many names.** `src/sremock.c` compiles to a single static
  `/usr/local/bin/sremock`. The runtime image symlinks every mocked command name
  to it; `argv[0]`'s basename selects the command. The 19 names are
  `systemctl`, `journalctl`, `ps`, `ss`, `curl`, `df`, `free`, `top`, `du`,
  `nginx`, `getenforce`, `ausearch`, `dig`, `nslookup`, `ping`, `nc`, `docker`,
  `podman`, `kubectl`.
- **PATH ordering is part of the contract.** The troubleshoot image sets
  `PATH="/usr/local/bin:…"` so the mocks shadow both BusyBox applets in `/bin`
  and any package tool in `/usr/bin`.
- **Fixtures are flat files.** The generator writes 32 named text files; the
  builder copies them to `/var/lib/sremock/current/` (root-owned, directories
  0755, files 0644 so the trainee can read them). `sremock` copies the selected
  file's bytes to stdout unchanged — no prefix, no added newline, CRLF and a
  missing final newline are preserved. When selected stages share evidence,
  the generator composes it in a canonical order: socket, Kubernetes and
  container table rows are combined, inspection arrays retain every object,
  and labelled journal, enforcement/AVC, memory, container-log, status, DNS,
  ping, nc and Kubernetes describe/log reports stay intact. Every selected stage
  therefore keeps its own clues regardless of `PICK` order.
- **Selection is a small explicit parser, not a shell.** `journalctl -u UNIT -n
  100 --no-pager` accepts those three flags in any order; `journalctl -xe` is
  its own form. `kubectl` distinguishes `get pods`, `get svc [NAME]`,
  `get endpoints [NAME]`, `get events`, `get configmap NAME`, `get deploy NAME`,
  `describe pod NAME` and `logs POD`.
  `docker`/`podman` distinguish `ps -a`, `logs` and `inspect`. The general
  triage commands have one fixture each, selected by their documented form:
  `ps aux`, `ss -tlnp`, `df -h`, `free -m`, `top`, `du -sh DIR`, `nginx -t`,
  `getenforce`, `ausearch -m avc`, `curl -v URL`, `curl -I URL`, `nc -zv HOST
  PORT`, and `dig` / `nslookup` / `ping`, which answer any argument list from
  their single fixture.
- **The fixture name is a compile-time constant.** Arguments never become part
  of a path, so no argument can traverse out of the fixture root or select
  another file.
- **Unsupported invocations are honest failures.** Anything outside the forms
  above exits nonzero with `sremock: unsupported invocation: <command>` on
  stderr and no stdout. A missing or unreadable fixture exits nonzero with
  `sremock: cannot read <path>: …`. The mock never invents output.
- **`SREMOCK_ROOT`** overrides the fixture root and exists for host testing
  (`tests/troubleshoot_smoke.sh` uses it). It is not a runtime configuration
  knob; inside the image the default root is the only one used.

## Running it

```sh
make up PROFILE=troubleshoot                         # seeded selection of all sixteen
# Or choose an exact, ordered subset:
make up PROFILE=troubleshoot PICK=disk-full,container-crash
make ssh
```

- `PICK` names any non-empty subset and its exact order for this build
  (`--pick` in the generator). With `PICK` set, `SET` does not decide the count;
  the picked list does.
- With no `PICK`, this profile defaults to `SET=16`, selecting all sixteen
  templates in seeded order. `SET=1` through `15` selects that many from the
  pool; values outside 1–16 are rejected before the build. The terminal profile
  keeps its default `SET=8`.
- `SEED=` and `PORT=` work as in the default profile. A new seed rebuilds the
  corpus; the generator layer is keyed on the build arguments, so an unchanged
  `SEED` and `PICK` reuse the cached layer.
- `make up` prints the seed, port, credentials and the stage list. For this
  profile it also echoes `profile=troubleshoot pick=…`.

## Replay with a seed

The generated corpus is deterministic for a given `SEED` **and** `PICK`.

```sh
make down
make up PROFILE=troubleshoot PICK=disk-full,container-crash SEED=1234
```

- `make up` records the seed in `.instance/seed` and reuses it until you pass a
  new `SEED=` or run `make new`.
- Replay needs both halves. The same seed under a different `PROFILE` or `PICK`
  is a different challenge set — that is expected, not a lost seed.
- `make status` prints the stored seed and the container's stage list, which is
  the quickest way to confirm what you are replaying.
- `make down` copies `/var/lib/sre-trainer/state/records.jsonl` to `state/`
  before removing the container; `make best` lists those runs.

## Inspecting hints

Hints are checklists, not answers, and they are the fastest way to see which
mock command the stage expects next:

```sh
verify next        # the current stage brief, with check ids and submit forms
verify hint        # hint 1 of the stage
verify hint 2      # the reason and the commands for this stage
verify hint 9      # show everything for the stage
verify status      # stage, elapsed time, checks done
```

## Knowing when the profile is solved

The flow is the default profile's flow, per stage:

1. Read the brief (`verify next`). It lists every check id and its submit form.
2. Submit hash checks with `verify check <check-id> <answer>`. Answers are the
   literal tokens the evidence prints (pairs are joined with `:` in the order
   the brief states).
3. For a file check, repair the named file **in place**, then run
   `verify check <check-id> <path>`. The trailing argument is accepted but
   ignored: the verifier hashes the file at the checked path and compares it
   with the expected corrected bytes. Extra edits, reordering lines or changing
   the trailing newline all fail.
4. Checks may be submitted in any order. `verify stage` prints the stage flag
   once every check in the stage passes; `verify unlock <flag>` advances.
5. After the last stage is unlocked, `verify finish <final-flag>` records the
   run. Then `make down` (save the record) and `make best` (compare runs).

A stage is solved when `verify stage` prints its flag — not when the service
"looks fixed", because nothing is actually restarted.

## Limitations

- **Mocked, frozen state.** The fixtures are static text captured at build time.
  Nothing restarts, logs do not grow, ports do not open or close. Re-running a
  command prints exactly the same bytes.
- **Narrow command surface.** Only the invocations described above are
  implemented. Anything else — `systemctl restart`, `kubectl apply`,
  `docker run`, `ss -tulpn` — fails on purpose. A mock that guesses would teach
  the wrong reflex.
- **No real systemd, kubelet, API server, web server, SELinux enforcement or
  container runtime.** The image does not install them; `nginx`, `docker` and
  `podman` are mocks too. The SELinux drill teaches fixture interpretation, not
  live policy changes.
- **No network.** The drills need none, and the image is built without it at run
  time.
- **Repairs are verified by hash, not by behaviour.** You cannot "fix" the
  scenario and re-run the service to confirm it; the file check compares the
  bytes to the expected corrected file. That is why the briefs describe the
  exact end state.
- **Throwaway container.** Your edits live inside the container. `make down`
  removes it, so unrecorded work is gone; the completion record is the only
  thing that persists.
- **Fixed pool.** There are sixteen templates. Fixtures for commands the picked
  stages do not use are still shipped, but they print a neutral placeholder
  instead of scenario data.

## Extending the pool later

The pool is designed so a new scenario is additive:

1. **Template** — in `generator/generate.py`, add a
   `@troubleshoot_template("<id>")` function that returns
   `(instruction, hints, checks)`, and register the id in `TROUBLESHOOT_IDS`.
   Use `hash_check` for tokens the evidence prints and `file_check(path, fixed,
   fragment, hint, initial=broken)` for in-place repairs; `initial=` makes the
   generator refuse a check that already ships solved.
2. **Fixtures** — emit evidence with `put_fixture(fixtures, name, content,
   tid)`. Every name in `FIXTURE_NAMES` is written for every build. Conflicting
   hard claims fail loudly, except for table/transcript evidence that explicitly
   uses a tested `put_fixture_merged` combiner. A template that can yield a shared
   fixture uses `put_soft`, and a hard claim always wins. Unused names fall back
   to the neutral placeholder.
3. **New command or new argument form** — add the dispatch arm in
   `src/sremock.c` (fixture names stay compile-time constants), add the symlink
   in the `troubleshoot` stage of the `Dockerfile` for a brand-new command
   name, and keep `PATH` ordering intact.
4. **Tests and docs** — extend the dispatch table and fixtures in
   `tests/troubleshoot_smoke.sh`, add the mapping to this document, and run
   `sh tests/troubleshoot_smoke.sh` plus
   `python3 generator/generate.py --self-test <seeds>`.
