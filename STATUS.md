# STATUS

Phase 1 complete: scaffold, generator, verifier, docs, defect fixes and a full dynamic
end-to-end acceptance run.

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

## Verification (2026-11-23, this host, Docker 29.9.3)

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
- sshd is reachable on loopback only: the container listens on 0.0.0.0:22 (needed for
  Docker's port forwarding) and the host publish is `127.0.0.1:PORT`.

## Pending / notes

- `docker logs` shows `Unsupported option UsePAM` from Alpine's non-PAM sshd; `UsePAM no`
  is ignored there and password auth works (left as documentation of intent).
- Host `state/records.jsonl` holds the most recent container's records only; merging runs
  across sessions is an open question in `PLAN.md`.
