# STATUS

Phase 1 — repo scaffold, build system, container image and docs.

## Done

- T01: repo skeleton — `.gitignore`, `.dockerignore`, `LICENSE` (MIT), `README.md`,
  `PLAN.md`, `STATUS.md`.
- T01: multi-stage `Dockerfile` (pinned `alpine:3.24.4`): builder runs the generator and
  compiles the static verifier; runtime ships challenges, setuid verifier, root-only
  corpus and sshd, with no `answers.json`.
- T01: `docker/entrypoint.sh` (user from `SSH_USER`/`SSH_PASSWORD`, ownership, exec) and
  `docker/sshd_config` (password auth, no root login, `UsePAM no`).
- T01: `Makefile` with `up`, `down`, `ssh`, `new`, `status`, `best`, `test`, `clean`;
  credentials in `.instance/creds` (0600), records in `state/`.

## In progress

- T04: end-to-end acceptance run (`make test`) and the spoiler-free walkthrough
  (`docs/walkthrough.md`, currently a placeholder link in the README).

## Pending

- T02: `generator/generate.py` — implementation has landed; needs an integration pass
  against the image build (artifact names, modes, set size).
- T03: `src/verify.c` (+ `sha256.c`, `util.c`) — implementation has landed; needs an
  integration pass (setuid behaviour, state layout, record schema).

## Key decisions

- Alpine patch pinned to `3.24.4`; streaming `latest` is not acceptable for reproducible
  drills.
- Answers are generated and deleted inside the builder; the runtime image only holds
  hashes and flag digests.
- Challenge files are installed at their real absolute paths with generator modes intact;
  `/home/trainee` alone is re-owned to uid/gid 1000.
- The trainee is uid/gid 1000 with no `sudo`; everything privileged goes through the
  setuid `verify`.
- Records: `/var/lib/sre-trainer/state/records.jsonl` in the container, copied to
  `state/records.jsonl` on the host by `make down`.

## Verification

- `make help` lists every target; `make up` builds, starts and prints seed/port/creds.
- `make test` performs an up/down cycle and asserts `git status --porcelain` is empty.
- Build and login were exercised on this host (see `.agent/reports/T01-scaffold.md`).
