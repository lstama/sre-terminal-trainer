# STATUS

Phase 1 complete: scaffold, generator, verifier, docs, defect fixes and a full dynamic
end-to-end acceptance run. Phase 2: the harness runs on Docker **or** Podman (rootless
included), with a real 8/8 solve under Podman.

## Done

- T01: repo skeleton (`.gitignore`, `.dockerignore`, MIT `LICENSE`, `README.md`, `PLAN.md`),
  multi-stage `Dockerfile` (pinned `alpine:3.24.4`), `docker/entrypoint.sh`,
  `docker/sshd_config`, and the `Makefile` (`up`, `down`, `ssh`, `new`, `status`, `best`,
  `test`, `clean`).
- T02: `generator/generate.py` — 16 templates, deterministic `SEED`/`SET` selection,
  `--dump-answers` for tests only, and a `--self-test SEEDS` mode that regenerates a full
  set per seed and re-derives every answer from the artefacts on disk.
- T03: `src/verify.c` (+ `sha256.c`, `util.c`) — static setuid verifier; `next`, `check`,
  `stage`, `unlock`, `finish`, `hint`, `status`, `reset`; root-only manifest/stages/state.
- T04: read-only acceptance review (`.agent/reports/T04-review.md`).
- T05: all review findings fixed; `make test` and the full dynamic acceptance run executed;
  real `docs/walkthrough.md` written. Details: `.agent/reports/T05-fix-verify.md`.
- T06: read-only static recheck (`.agent/reports/T06-final-review.md`).
- T07: Docker/Podman engine abstraction in the `Makefile` (`ENGINE`, auto-detected), docs
  updated, Podman 6.2.0 installed rootless, and both engines verified live.
  Details: `.agent/reports/T07-podman.md`.
- Day 2: the `troubleshoot` profile has twelve templates (`svc-crashloop`,
  `k8s-crashloop`, `disk-full`, `container-crash`, `nginx-config`, `dns-mismatch`,
  `selinux-avc`, `port-conflict`, `oom-kill`, `image-pull`, `svc-selector`,
  `resource-limits`). Diagnostic commands are mocked; reference ownership is
  real image metadata. Details: `docs/troubleshooting.md`.
- T26 (`feat/troubleshoot-wave3`): added `nginx-config` and `dns-mismatch`, expanded
  the default troubleshoot pool to six, and extended docs/smoke/self-test coverage.
  Docker verification includes a full two-stage SSH solve with both file repairs;
  `make test` passes 111 verifier and 369 troubleshoot assertions.
- T28 (`feat/troubleshoot-wave3`): fixed the literal `%%` in the generated DNS ping
  fixture and made `PICK` override troubleshoot `SET` range validation. Verified
  `PICK=nginx-config,dns-mismatch SET=8` with a full two-stage Docker SSH solve and
  record save; no-PICK `SET=7` is still rejected before side effects.
- T30 (`feat/troubleshoot-wave4`): added `selinux-avc` and `port-conflict`, expanded
  the default troubleshoot pool/SET to eight, and added deterministic composition
  for shared socket/curl fixtures. Host generator, smoke and determinism checks
  pass. Docker SSH verification solved both stages with both repairs and saved one
  record. `make test` passes 111 verifier and 547 troubleshoot assertions; a default
  Docker troubleshoot boot also reports eight stages and 30 fixtures.
- T33 (`feat/troubleshoot-wave5`): added `oom-kill` and `image-pull`, expanded the
  default troubleshoot pool/SET to ten, and added deterministic shared-fixture
  composition. All 1,023 non-empty subsets passed in forward/reverse order with
  30 fixtures each. Docker SSH solved both stages with both repairs and saved one
  record; a default boot reports ten stages. `make test` passes 111 verifier and
  710 troubleshoot assertions with a clean tree.
- T35 (`feat/troubleshoot-wave6`): added `svc-selector` and `resource-limits`,
  expanded the default troubleshoot pool/SET to twelve and the fixture contract
  to 32, and added canonical shared-Kubernetes evidence composition. All 4,095
  non-empty subsets passed in forward/reverse order (8,190 runs) with 32 fixtures
  each. Docker SSH solved both stages with both YAML repairs and saved one record;
  a default boot reports twelve stages. `make test` passes 111 verifier and 1,026
  troubleshoot assertions with a clean tree.

## Fixes landed in T05

- `top-talker`: the answer is now provably the busiest client — one IP at 1500–1900
  requests, 30–45 decoys each below it; the template re-reads the written log, recomputes
  the winner and aborts generation if it differs from the stored answer (no `sess=` marker
  giveaway either).
- `config-drift`: the baseline token now exists only in `app.conf.bak`; the shipped
  `app.conf` is asserted not to contain it.
- `Makefile`: sshd is published on `127.0.0.1:PORT` only; `up` fails loudly if the stage
  list cannot be read; `ssh` targets `127.0.0.1`.
- `verify finish` is idempotent-safe: a `state/finished` marker refuses a second record;
  `verify reset` clears the marker.
- `log-growth` sizes and the `perm-audit` credential filename are randomized per seed.
- `entrypoint.sh`: `SSH_USER` is matched literally (no grep BRE injection); home is a
  variable.
- Docs: `docs/walkthrough.md` (spoiler-free tour of all 16 templates), README accuracy
  (hints, toolbox/network wording, loopback-only port), PLAN state layout and task table.

## Verification — Docker (T05, 2026-11-23, Docker 29.9.3)

- `sh tests/smoke.sh`: 111 assertions, 0 failures (includes the new double-`finish` guard).
- `make up SEED=4242 SET=8` → container inspected: bash/tmux/nano/grep/tree/less/diff/gawk/
  find present, no python/gcc/sudo, trainee uid 1000 non-root, `verify` 4755 root:root,
  corpus 0700 dirs / 0600 files unreadable as trainee, no `answers.json` in the image,
  port bound to `127.0.0.1` only.
- Complete in-container solve as uid 1000 with host answers: 8/8 stages, every check
  (hash and file), wrong-answer and wrong-flag rejections, hints, status, unlock chaining,
  `finish` accepted then refused on repeat. Repeated end-to-end for `SEED=4243` (includes
  `top-talker`, `crlf-fix`, `cut-paste`, `copy-mode`): 8/8.
- `make down` copied `state/records.jsonl` (seed, elapsed, stage list) to the host, removed
  the container, and `git status --porcelain` stayed clean.
- Determinism: same seed twice → `diff -r` empty and identical aggregate checksum;
  different seed → different selection and order.
- `make test`: up/down cycle with a clean tree after commit.

## Verification — Docker + Podman (T07, 2026-11-23)

Host: CachyOS, kernel 7.3.3, Docker 29.9.3 (rootful daemon) and Podman 6.2.0 (rootless;
`podman info` maps 0→1000 plus 1→100000:65536, overlay graph driver, netavark). Podman was
installed with `pacman -S podman`; `/etc/subuid` and `/etc/subgid` already carried
`lstama:100000:65536` and `newuidmap`/`newgidmap` carry `cap_setuid`/`cap_setgid`, so no
other system configuration was changed.

- `make test` (auto-detected podman, `PORT=2223`) and `make test ENGINE=docker PORT=2224`
  both end with `==> OK: image builds, container runs, tree is clean`.
- `make up PORT=2223` under Podman builds the unchanged `Dockerfile` with buildah 1.46
  (both stages, `ssh-keygen -A` included), starts the container, reads the stage list back
  and publishes `127.0.0.1:2223->22/tcp`; `ss -ltn` shows the listener on `127.0.0.1:2223`
  only (rootless forwarder, no 0.0.0.0 bind). `.dockerignore` is honoured by buildah.
- Inside the Podman container: `verify` is `-rwsr-xr-x root root`; `/var/lib/sre-trainer/
  manifest` gives `Permission denied` for uid 1000; `podman exec -u 1000` reports
  `uid=1000(trainee)` while `podman exec` (default) reports `uid=0(root)`.
- Real SSH password session from the host to `127.0.0.1:2223` as `trainee` (non-interactive
  with `SSH_ASKPASS`+`setsid`, same `ssh` flags as `make ssh`): `id` → trainee,
  `verify next` prints the stage brief, `verify status`, `verify hint`, a wrong answer
  rejected with rc 1, a correct check accepted, `verify stage` produced
  `FLAG{18882a3da581}`, `verify unlock` advanced to stage 2.
- Full 8/8 solve as uid 1000 under Podman (seed 4242, order conf-hunt … deploy-error): 77
  asserted steps, 0 failures — every hash and file check (20 000-line line replacement,
  CRLF fix, config-drift restore, cut-paste reorder written byte-exact through stdin),
  repeat-check rejection, wrong-flag rejection, all stage flags parsed and matched, `finish`
  accepted then refused on repeat.
- `make status`, `make down` (record copied to `state/records.jsonl`), `make clean`
  (container + image removed, `.instance/` and `state/` deleted) and a fresh
  `make up SEED=4243` (new stage order) all work under Podman; `git status --porcelain`
  stayed empty throughout.
- Record copied out by `make down`:
  `{"epoch":1795433936,"elapsed_seconds":7,"seed":4242,"stages":["conf-hunt", … ]}`.

## Key decisions

- Alpine patch pinned to `3.24.4`; streaming `latest` is not acceptable for reproducible
  drills.
- Answers are generated and deleted inside the builder; the runtime image holds hashes and
  flag digests only.
- Challenge files are installed at their real absolute paths with generator modes intact;
  `/home/trainee` alone is re-owned to uid/gid 1000.
- Trainee is uid/gid 1000 with no `sudo`; everything privileged goes through setuid
  `verify`.
- Records live at `/var/lib/sre-trainer/state/records.jsonl` and are copied to
  `state/records.jsonl` by `make down` (each `down` replaces the host copy; see PLAN open
  questions).
- sshd is reachable on loopback only: the container listens on 0.0.0.0:22 (needed for the
  engine's port forwarding) and the host publish is `127.0.0.1:PORT`.
- **One engine switch**: `ENGINE ?= $(shell … podman … docker)` in the `Makefile`; every
  target uses `$(ENGINE)` and both CLIs accept the exact flags used
  (`build --build-arg`, `run -d -p`, `exec`, `ps --filter/--format`, `logs`, `inspect`,
  `cp`, `image inspect`, `rm -f`, `rmi -f`), so no engine-specific branches were added.
  A rootless Podman container maps container-root into the user namespace, where the 4755
  verifier still reaches root-owned stage data — no change to the security model.

## Pending / notes

- `docker logs` (and `podman logs`) shows `Unsupported option UsePAM` from Alpine's
  non-PAM sshd; `UsePAM no` is ignored there and password auth works (left as
  documentation of intent).
- Host `state/records.jsonl` holds the most recent container's records only; merging runs
  across sessions is an open question in `PLAN.md`. A `make down` of a container that never
  reached the final flag still replaces the host copy with the (empty) container file.
- **Stale Docker Hub credential (host, not the repo)**: Podman reads
  `~/.docker/config.json`, so an expired `index.docker.io` entry makes the pull fail with
  `unauthorized: incorrect username or password` where Docker silently falls back to an
  anonymous pull. On this host the Podman runs used `REGISTRY_AUTH_FILE` pointing at an
  empty auth file; the stored entry should be refreshed with `podman login docker.io` or
  removed. README troubleshooting documents this.
- Podman tags images under `localhost/`, so `make status` shows
  `localhost/sre-terminal-trainer:local` where Docker shows `sre-terminal-trainer:local`
  (cosmetic; `make up/down/test` all work with the plain tag).
