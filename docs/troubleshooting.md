# Day 2: the troubleshoot pilot

The trainer's second profile is a small set of diagnostic drills whose terminal
commands are answered from fixtures instead of a live machine. It exists to
practise the read-then-repair loop — inspect the evidence, decide on one change,
make it, prove it — without shipping a privileged container, a systemd host or a
Kubernetes cluster.

Everything outside the drills is unchanged: the same sshd/entrypoint image
layout, the same setuid `verify`, the same `/var/lib/sre-trainer/state/`
progress and the same flag, timing and record flow. If you have used the default
profile, the interaction is identical; only the stages differ.

- Profile flag: `PROFILE=troubleshoot`
- Pilot stages: `svc-crashloop` and `k8s-crashloop`
- Image / container: `sre-terminal-trainer-troubleshoot:local` / `sre-trainer-troubleshoot`
  (separate from the default names, so both profiles can run side by side)

## The two pilot stages

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
- **Fixtures are flat files.** The generator writes 30 named text files; the
  builder copies them to `/var/lib/sremock/current/` (root-owned, directories
  0755, files 0644 so the trainee can read them). `sremock` copies the selected
  file's bytes to stdout unchanged — no prefix, no added newline, CRLF and a
  missing final newline are preserved.
- **Selection is a small explicit parser, not a shell.** `journalctl -u UNIT -n
  100 --no-pager` accepts those three flags in any order; `journalctl -xe` is
  its own form. `kubectl` distinguishes `get pods`, `get events`, `get
  configmap NAME`, `get deploy …`, `describe pod NAME` and `logs POD`.
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
make up PROFILE=troubleshoot PICK=svc-crashloop,k8s-crashloop
make ssh
```

- `PICK` names the exact stages and their order for this build (`--pick` in the
  generator). With `PICK` set, `SET` does not decide the count; the picked list
  does.
- With no `PICK`, the build takes a seeded random sample of the pilot pool, so
  `SET` must be within the pool size — `make up PROFILE=troubleshoot SET=2`.
  The default `SET=8` belongs to the terminal profile and the generator rejects
  it for this two-stage pool, so pass `SET` explicitly if you omit `PICK`.
- `SEED=` and `PORT=` work as in the default profile. A new seed rebuilds the
  corpus; the generator layer is keyed on the build arguments, so an unchanged
  `SEED` and `PICK` reuse the cached layer.
- `make up` prints the seed, port, credentials and the stage list. For this
  profile it also echoes `profile=troubleshoot pick=…`.

## Replay with a seed

The generated corpus is deterministic for a given `SEED` **and** `PICK`.

```sh
make down
make up PROFILE=troubleshoot PICK=svc-crashloop,k8s-crashloop SEED=1234
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
verify hint 2      # with the in-stage reason and the commands to run
verify hint 9      # show everything for the stage
verify status      # stage, elapsed time, checks done
```

## Knowing when the pilot is solved

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
- **No real systemd, kubelet, API server or container runtime.** The image does
  not install them; `docker` and `podman` are mocks too, not engines.
- **No network.** The drills need none, and the image is built without it at run
  time.
- **Repairs are verified by hash, not by behaviour.** You cannot "fix" the
  scenario and re-run the service to confirm it; the file check compares the
  bytes to the expected corrected file. That is why the briefs describe the
  exact end state.
- **Throwaway container.** Your edits live inside the container. `make down`
  removes it, so unrecorded work is gone; the completion record is the only
  thing that persists.
- **Fixed pool.** There are two pilot templates. Fixtures for commands the
  picked stages do not use are still shipped, but they print a neutral
  placeholder instead of scenario data.

## Extending the pool later

The pilot is designed so a new scenario is additive:

1. **Template** — in `generator/generate.py`, add a
   `@troubleshoot_template("<id>")` function that returns
   `(instruction, hints, checks)`, and register the id in `TROUBLESHOOT_IDS`.
   Use `hash_check` for tokens the evidence prints and `file_check(path, fixed,
   fragment, hint, initial=broken)` for in-place repairs; `initial=` makes the
   generator refuse a check that already ships solved.
2. **Fixtures** — emit evidence with `put_fixture(fixtures, name, content,
   tid)`. Every name in `FIXTURE_NAMES` is written for every build, so names
   shared with another template must produce identical bytes or generation
   fails loudly; unused names fall back to the neutral placeholder.
3. **New command or new argument form** — add the dispatch arm in
   `src/sremock.c` (fixture names stay compile-time constants), add the symlink
   in the `troubleshoot` stage of the `Dockerfile` for a brand-new command
   name, and keep `PATH` ordering intact.
4. **Tests and docs** — extend the dispatch table and fixtures in
   `tests/troubleshoot_smoke.sh`, add the mapping to this document, and run
   `sh tests/troubleshoot_smoke.sh` plus
   `python3 generator/generate.py --self-test <seeds>`.
