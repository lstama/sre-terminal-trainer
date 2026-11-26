# PLAN — sre-terminal-trainer

A Docker/Podman-based SRE terminal practice harness: one build produces a randomized set
of hands-on drills, an SSH session to solve them, and a setuid helper that verifies answers
and records your time. Nothing is hosted; everything runs locally.

Reference detail for this phase: `.agent/reports/` — `T01-scaffold.md` (scaffold, build,
container and docs), `T02-generator.md` (challenge templates and file-check semantics),
`T03-verifier.md` (setuid verifier), `T04-review.md` (read-only acceptance review),
`T05-fix-verify.md` (defect fixes + dynamic end-to-end verification) and `T07-podman.md`
(engine abstraction, Podman install and both-engine verification).

## Goal

`make up` → SSH into the container → solve stages with `verify` → `make down` keeps your
records. Re-running with the same seed replays the same challenge set; `make new` gives a
new one.

Non-goals: no web UI, no scoring service, no `sudo` for the trainee, no answer material in
the repo or the image. The container is not network-isolated at the engine level (the
published sshd port is bound to loopback, and the image simply ships no tooling that needs
egress); the drills are all local files.

## Architecture

| Piece | Where | When it runs |
|---|---|---|
| Challenge generator (Python) | `generator/generate.py` | container **builder** stage only |
| Verifier (C, static, setuid root) | `src/verify.c` (+ `sha256.c`, `util.c`) | compiled in builder, run by the trainee |
| Container image | `Dockerfile` (multi-stage, pinned `alpine:3.24.4`) | built by `make up` |
| Runtime entrypoint | `docker/entrypoint.sh` | container start: user from `SSH_USER`/`SSH_PASSWORD`, ownership, exec sshd |
| sshd config | `docker/sshd_config` | password auth, root login off, `UsePAM no` |
| Host orchestration | `Makefile` | `up`, `down`, `ssh`, `new`, `status`, `best`, `test`, `clean` via `$(ENGINE)` |

### Data flow

1. `MODE=terminal|incident` is the public selector. The Makefile maps Incident mode to the
   existing internal `PROFILE=troubleshoot` build arg, which the Dockerfile forwards to the
   generator as `--profile troubleshoot`. Direct generator calls can use canonical
   `--mode terminal|incident`; both spellings normalize to the same internal profile.
   Build args `SEED` and `SET` drive
   `python3 generator/generate.py --seed SEED --set N --out /generated
   --dump-answers /generated/answers.json`.
2. The builder produces `/generated/{fs,manifest,stages/NN/*,final.sha256,valid_names.inc}`,
   compiles `verify` and the fixture dispatcher `sremock` (the latter includes the
   build-time name table), and deletes `answers.json`.
3. The runtime stage copies `fs/` onto `/` (challenge files live at their real absolute
   paths, modes preserved), installs the verifier as `/usr/local/bin/verify` (4755
   root:root), and the root-only corpus at `/var/lib/sre-trainer/` (0700 dirs / 0600
   files). `/home/trainee` is owned by uid/gid 1000 so the trainee can edit its files.
4. `$(ENGINE) run` (docker or podman) publishes `127.0.0.1:PORT` → `22` (loopback only)
   and passes `SSH_USER`/`SSH_PASSWORD`; the entrypoint creates the account (uid 1000, home
   `/home/trainee`, `/bin/bash`) and starts sshd.
5. `verify` reads the manifest/stages as root, writes progress and a JSONL record under
   `/var/lib/sre-trainer/state/`. `make down` copies `state/records.jsonl` to the host.

### Interfaces (do not change casually)

- Generator CLI: `--seed INT --set N --out DIR --dump-answers FILE`
  `[--mode terminal|incident]`; `--self-test SEEDS` regenerates a full set per seed and
  re-derives every answer from the artefacts on disk.
- Generator output: `fs/` mirrors container-absolute paths; `manifest` is line based
  (`MODE <terminal|incident>`, `SEED n`, `STAGES n`, `STAGE <id>` repeated; a missing MODE
  line is read as `terminal` for backward compatibility); `stages/NN/{instruction,hint,
  checks.tsv,flag.sha256}`; `final.sha256`.
- Verifier CLI: `next`, `check <id> <answer>`, `stage`, `unlock <flag>`,
  `finish <final-flag>`, `hint [n]`, `status`, `reset`, `--help`.
- State: `/var/lib/sre-trainer/state/{progress,clock,hints,stage_NN.frag,stage_NN.done,
  finished,records.jsonl}`; records are
  `{"epoch","elapsed_seconds","seed","mode","hints_used","stages"}` (old records without
  the two new fields stay readable). `hints` counts hint pages shown this attempt;
  `finished` marks a recorded run so a second `verify finish` cannot append a duplicate;
  `verify reset` clears both it and the hint count along with the progress files.
- Host creds: `.instance/creds` with `SSH_USER=`/`SSH_PASSWORD=` lines, mode 0600.

## Decisions

- **Pin the Alpine patch** (`3.24.4`, current at scaffold time) so rebuilds do not drift
  musl/OpenSSH/coreutils underneath the drills.
- **Generate at build time, answer key never shipped**: `answers.json` is deleted in the
  builder; the image carries hashes only. Seed-specific mock resource names live in a
  generated C include compiled into `sremock`, never in a runtime data file.
- **Challenge files are installed at their absolute paths**, not under a sandbox root:
  the generator emits paths like `/home/trainee/<drill>` and `/var/log/sre/<log>`.
  Modes are preserved (a perm drill inspects them); only `/home/trainee` changes owner.
- **One script in the image** (`docker/entrypoint.sh`); everything else is a binary,
  package or generated data.
- **uid/gid 1000** for the trainee, home `/home/trainee`, shell `/bin/bash`, no `sudo`.
- **Records live in `state/records.jsonl`** (the verifier's documented layout) and are
  copied to the host as `state/records.jsonl`; both are gitignored.
- **One engine variable, no engine-specific branches**: every target invokes `$(ENGINE)`
  (podman when on `PATH`, else docker, overridable with `ENGINE=…`). Docker and Podman
  accept the same `build`, `run -d -p`, `exec`, `ps --filter/--format`, `logs`, `inspect`,
  `cp`, `image inspect`, `rm -f` and `rmi -f` invocations used here, so no conditional code
  was needed; the loopback publish and the 4755 verifier behave identically (rootless
  Podman maps container-root into the user namespace, where setuid still works).
- **Host credentials are generated per instance** by `up` (mode 0600, never committed).
- **Terminal mode and Incident mode are peer practice modes.** `MODE=incident` reuses the
  same harness for sixteen diagnostic templates (`svc-crashloop`, `k8s-crashloop`,
  `disk-full`, `container-crash`, `nginx-config`, `dns-mismatch`, `selinux-avc`,
  `port-conflict`, `oom-kill`, `image-pull`, `svc-selector`, `resource-limits`,
  `tls-expiry`, `dependency-down`, `blocked-port`, `perm-denied`). Their service commands
  are mocked; `ls -l`/`stat` read real ownership metadata on reference assets. Shared
  socket, curl, journal, enforcement/AVC, resource, container and Kubernetes fixtures
  compose evidence when multiple selected stages need them. The old `PROFILE=troubleshoot`
  and `--profile troubleshoot` spellings remain hidden compatibility aliases. Details:
  `docs/incident-mode.md`.

## Task split

| Task | Scope | State |
|---|---|---|
| T01 | Repo skeleton, Dockerfile, entrypoint/sshd config, Makefile, docs | done |
| T02 | `generator/generate.py` — challenge templates, artifact layout, `--self-test` | done |
| T03 | `src/verify.c` (+ helpers) — setuid verifier and state machine | done |
| T04 | End-to-end acceptance review (read-only) | done — findings in T04 report |
| T05 | Fix review findings, dynamic end-to-end verification, walkthrough | done |
| T06 | Read-only static recheck of the fixed harness | done |
| T07 | Docker/Podman engine abstraction, Podman install, both-engine verification | done |
| T30 | Add `selinux-avc` and `port-conflict`; expand Incident mode pool to eight | done |
| T33 | Add `oom-kill` and `image-pull`; expand Incident mode pool to ten | done |
| T35 | Add `svc-selector` and `resource-limits`; expand Incident mode pool to twelve | done |
| T37 | Add `tls-expiry` and `dependency-down`; expand Incident mode pool to fourteen | done |
| T39 | Add `blocked-port` and `perm-denied`; expand Incident mode pool to sixteen | done |
| T46 | Mock aliases, seed-validated not-found errors and fixture-free command help | done |
| T48 | Bare kubectl list aliases and accurate df help text | done |
| T53 | Terminal mode / Incident mode naming, hidden aliases and README restructure | done |
| T56 | Mode-aware timing records (`MODE` manifest header, `state/hints`, per-mode `make best`) | done |

## Open questions

- Should the host keep a merged (append across sessions) record log instead of the last
  container's records? Current behaviour: each `make down` replaces `state/records.jsonl`.
- Should `make up` also print the stage instruction titles, or is the stage id list enough?

## Future improvements

Ranked by value; each line is a one-line scope plus its rationale.

1. **Polishment** — Consolidate the duplicated two-mode README bullets and add a "start with Terminal mode, then Incident mode" line; rationale: the intro repeats each mode twice and never tells newcomers which mode to try first.
2. **Feature completeness** — Mode-aware timing records and per-mode best times (this task); rationale: records had no mode, so `make best` mixed Terminal and Incident runs in one leaderboard. **DONE (T56).**
3. **New feature** — Exam simulation (`make exam MODE=incident SET=8`) with hints disabled/counted and a scorecard + replay command; rationale: a timed, hint-free run with repeatable grading is the natural next step now that per-mode records exist.
