# PLAN — sre-terminal-trainer

A Docker-based SRE terminal practice harness: one build produces a randomized set of
hands-on drills, an SSH session to solve them, and a setuid helper that verifies answers
and records your time. Nothing is hosted; everything runs locally.

Reference detail for this phase: `.agent/reports/T01-scaffold.md` (scaffold, build,
container and docs) plus the T02/T03 reports.

## Goal

`make up` → SSH into the container → solve stages with `verify` → `make down` keeps your
records. Re-running with the same seed replays the same challenge set; `make new` gives a
new one.

Non-goals: no web UI, no scoring service, no network access from inside the container, no
`sudo` for the trainee, no answer material in the repo or the image.

## Architecture

| Piece | Where | When it runs |
|---|---|---|
| Challenge generator (Python) | `generator/generate.py` | Docker **builder** stage only |
| Verifier (C, static, setuid root) | `src/verify.c` (+ `sha256.c`, `util.c`) | compiled in builder, run by the trainee |
| Container image | `Dockerfile` (multi-stage, pinned `alpine:3.24.4`) | built by `make up` |
| Runtime entrypoint | `docker/entrypoint.sh` | container start: user from `SSH_USER`/`SSH_PASSWORD`, ownership, exec sshd |
| sshd config | `docker/sshd_config` | password auth, root login off, `UsePAM no` |
| Host orchestration | `Makefile` | `up`, `down`, `ssh`, `new`, `status`, `best`, `test`, `clean` |

### Data flow

1. Build args `SEED` and `SET` drive `python3 generator/generate.py --seed SEED --set N
   --out /generated --dump-answers /generated/answers.json`.
2. The builder produces `/generated/{fs,manifest,stages/NN/*,final.sha256}`, compiles
   `gcc -O2 -static -o /generated/verify src/*.c`, and deletes `answers.json`.
3. The runtime stage copies `fs/` onto `/` (challenge files live at their real absolute
   paths, modes preserved), installs the verifier as `/usr/local/bin/verify` (4755
   root:root), and the root-only corpus at `/var/lib/sre-trainer/` (0700 dirs / 0600
   files). `/home/trainee` is owned by uid/gid 1000 so the trainee can edit its files.
4. `docker run` publishes `PORT:22` and passes `SSH_USER`/`SSH_PASSWORD`; the entrypoint
   creates the account (uid 1000, home `/home/trainee`, `/bin/bash`) and starts sshd.
5. `verify` reads the manifest/stages as root, writes progress and a JSONL record under
   `/var/lib/sre-trainer/state/`. `make down` copies `state/records.jsonl` to the host.

### Interfaces (do not change casually)

- Generator CLI: `--seed INT --set N --out DIR --dump-answers FILE`.
- Generator output: `fs/` mirrors container-absolute paths; `manifest` is line based
  (`SEED n`, `STAGES n`, `STAGE <id>` repeated); `stages/NN/{instruction,hint,checks.tsv,
  flag.sha256}`; `final.sha256`.
- Verifier CLI: `next`, `check <id> <answer>`, `stage`, `unlock <flag>`,
  `finish <final-flag>`, `hint [n]`, `status`, `reset`, `--help`.
- State: `/var/lib/sre-trainer/state/{progress,clock,stage_NN.frag,stage_NN.done,
  records.jsonl}`; records are `{"epoch","elapsed_seconds","seed","stages"}`.
- Host creds: `.instance/creds` with `SSH_USER=`/`SSH_PASSWORD=` lines, mode 0600.

## Decisions

- **Pin the Alpine patch** (`3.24.4`, current at scaffold time) so rebuilds do not drift
  musl/OpenSSH/coreutils underneath the drills.
- **Generate at build time, answer key never shipped**: `answers.json` is deleted in the
  builder; the image carries hashes only.
- **Challenge files are installed at their absolute paths**, not under a sandbox root:
  the generator emits paths like `/home/trainee/<drill>` and `/var/log/sre/<log>`.
  Modes are preserved (a perm drill inspects them); only `/home/trainee` changes owner.
- **One script in the image** (`docker/entrypoint.sh`); everything else is a binary,
  package or generated data.
- **uid/gid 1000** for the trainee, home `/home/trainee`, shell `/bin/bash`, no `sudo`.
- **Records live in `state/records.jsonl`** (the verifier's documented layout) and are
  copied to the host as `state/records.jsonl`; both are gitignored.
- **Host credentials are generated per instance** by `up` (mode 0600, never committed).

## Task split

| Task | Scope | State |
|---|---|---|
| T01 | Repo skeleton, Dockerfile, entrypoint/sshd config, Makefile, docs | done (this phase) |
| T02 | `generator/generate.py` — challenge templates and artifact layout | code landed, integration to confirm |
| T03 | `src/verify.c` (+ helpers) — setuid verifier and state machine | code landed, integration to confirm |
| T04 | End-to-end acceptance: `make test`, walkthrough doc | pending |

## Open questions

- Should the host keep a merged (append across sessions) record log instead of the last
  container's records? Current behaviour: each `make down` replaces `state/records.jsonl`.
- Should `make up` also print the stage instruction titles, or is the stage id list enough?
