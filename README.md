# sre-terminal-trainer

A disposable Docker **or Podman** container with randomized SRE practice in two modes, plus a `verify` helper that checks answers, hands out flag fragments and records your time — all local, nothing sent anywhere.

*Demo for **Terminal mode**: a full sample attempt with `make up`, `make ssh`, `verify next`, a grep on the deploy log, three verified answers, the stage flag and the next brief.*

![Demo for Terminal mode: a full sample attempt with three verified answers and the next stage brief](docs/demo/sample-run.gif)

[Watch the full Terminal mode demo video (MP4)](docs/demo/sample-run.mp4)

## Two practice modes

Both modes use the same local container, SSH session and `verify` loop. Terminal mode is the default; `MODE=terminal` also selects it explicitly.

```sh
make up                      # Terminal mode: eight stages by default
make up MODE=incident        # Incident mode: sixteen diagnostic stages
```

- **Terminal mode** — practise log triage, search and navigation with real files and a Linux toolbox; see the [spoiler-free walkthrough](docs/walkthrough.md).
- **Terminal mode** — repair configs and permissions, edit efficiently, and combine pipes and tmux; see the [drill reference](docs/walkthrough.md#3-what-the-drills-train).
- **Incident mode** — inspect mocked service, log, storage and network evidence, then make and verify a careful repair; see the [Incident mode guide](docs/incident-mode.md).
- **Incident mode** — work through container, Kubernetes, DNS, TLS and access-control scenarios without a live stack; see the [template and command reference](docs/incident-mode.md#the-sixteen-templates).

## Shared quick start

Build and start your chosen mode, then connect:

```sh
make up                      # or: make up MODE=incident
make ssh                     # type the password printed by make up
```

Inside either mode:

```sh
verify next                       # current stage brief
verify status                     # stage, elapsed time, checks done
verify hint                       # hints, a page at a time
verify check <check-id> <answer>  # submit one answer
verify stage                      # earn the stage flag once all checks pass
verify unlock <flag>              # advance to the next stage
verify --help                     # full command list
```

Finish the last stage with `verify finish <final-flag>`, log out, then:

```sh
make down        # copy completion records to state/records.jsonl, remove the container
make best        # your local runs, fastest first
```

For Incident mode, repeat `MODE=incident` on later host commands, for example `make down MODE=incident`, `make status MODE=incident` or `make new MODE=incident`. Match `ENGINE` and `PORT` too if you overrode them. `make new` starts a fresh seeded set; `make help` lists every target.

## What it is

- **Randomized challenges** — a fresh, seeded set of drills each build. A given `SEED` replays the same set; `make new` picks a new one.
- **Docker or Podman** — engine auto-detected (`podman` if on `PATH`, else `docker`); force either with `ENGINE=podman` / `ENGINE=docker`.
- **Real toolbox practice** — `bash`, `tmux`, `nano`, `less`, `tree`, `grep`, `gawk`, `find`. No `sudo`, no compilers, no root for the trainee.
- **Timing records** — `verify` tracks elapsed time; `make best` lists your local runs, fastest first.

> **Spoiler policy.** This README never gives answers or seed-specific facts. The answer key is generated in the container *build* stage and deleted before the runtime image is assembled — the image holds hashes only.

## Requirements

- Docker (Linux/macOS) or Podman (Linux, rootless OK; v4+), GNU `make` and `python3` on the host.
- A free local port (default `2222`).

## Make targets

| Target | What it does |
|---|---|
| `make up [MODE=terminal\|incident] [SEED=N] [SET=N] [PORT=N]` | Build the image, start the container, print seed/port/credentials and the stage list. |
| `make ssh` | SSH to `127.0.0.1:$PORT` with host-key checking disabled (throwaway container). |
| `make status` | Container state, credentials path, seed, port and saved records. |
| `make down` | Copy completion records to `state/`, then stop and remove the container. |
| `make new` | `down`, then `up` with a fresh seed. |
| `make best` | Local records, fastest first. |
| `make test` | Run the Terminal mode and Incident mode smoke suites, then one up/down cycle; assert the working tree is clean. |
| `make clean` | Remove the container, image, `.instance/` and `state/`. |

Variables: `MODE` (`terminal` or `incident`, default `terminal`), `ENGINE`, `IMAGE`, `CONTAINER`, `PORT`, `SET` (stages per run; default 8 for Terminal mode and 16 for Incident mode), `PICK` (comma-separated Incident mode template ids), `SEED`, `SSH_USER`, `INSTANCE` (credentials dir, default `.instance`) and `STATE` (records dir, default `state/`). Every target honours `ENGINE` and `MODE`, including the recursive `make` calls in `new` and `test`. The demo recorder uses `INSTANCE`/`STATE` to keep its own throwaway files.

## How it works

- `generator/generate.py` runs only at build time (`Dockerfile` builder stage) and writes challenge files, stage texts, check definitions and flag hashes; its answer dump is deleted in the builder.
- `src/verify.c` is compiled static and installed setuid root (4755) so it can read the root-only corpus; it drops privileges while parsing your answers. Progress and records live under `/var/lib/sre-trainer/state/`.
- `docker/entrypoint.sh` creates the non-root trainee account (uid 1000, no `sudo`) from `SSH_USER`/`SSH_PASSWORD`, fixes ownership and starts `sshd`.
- The runtime image ships no answer material and no tooling that needs network egress. Both engines build the same `Dockerfile` (Podman's buildah reads it and honours `.dockerignore` identically).

## Safety and privacy

- The sshd port is published on `127.0.0.1` only (loopback) — not reachable from the network. Keep it that way.
- Trainee is non-root (uid 1000) with no `sudo`; everything privileged goes through the setuid `verify`.
- Credentials are randomized per instance in `.instance/creds` (mode 0600). Don't reuse them elsewhere.
- Only sources, config and docs are tracked. `.instance/`, `state/` and the demo's `.demo-instance/`/`.demo-state/` are gitignored (see `.gitignore`, `.dockerignore`).

## Common issues

- **Port in use** — `make up PORT=2223`.
- **Docker permission denied** — add your user to the `docker` group.
- **Podman permission denied** — rootless Podman needs `subuid`/`subgid` ranges for your user; check `podman unshare id`, then add `<user>:100000:65536` to `/etc/subuid` and `/etc/subgid` and run `podman system migrate` once.
- **Podman `unauthorized: incorrect username or password` on pull** — a stale Docker Hub login in `~/.docker/config.json` (Docker silently falls back to anonymous, Podman does not); run `podman login docker.io`, remove that entry, or set `REGISTRY_AUTH_FILE` to an empty auth file.
- **`no container named …` after switching modes or engines** — each mode has its own container name; pass the matching `MODE` and `ENGINE` to `make down`, `make status` and `make clean`.
- **Container exits immediately** — `make up` prints the last log lines; the entrypoint refuses to start without `SSH_PASSWORD`.
- **Want the pristine challenge set back after editing** — `make down && make up` rebuilds it (same mode and seed unless you change them).

## More

- Terminal mode: [spoiler-free guided walkthrough](docs/walkthrough.md).
- Incident mode: [template list, command reference and verify flow](docs/incident-mode.md).
- Re-record the demo (VHS, tapes, fixed seed, GIF/MP4): [docs/demo/README.md](docs/demo/README.md).

## License

MIT — see [LICENSE](LICENSE).
