# sre-terminal-trainer

A disposable Docker **or Podman** container with randomized SRE terminal drills (log triage, config fixes, permission audits, editors, pipes, tmux) and a `verify` helper that checks answers, hands out flag fragments and records your time — all local, nothing sent anywhere.

![A full sample attempt: make up, make ssh, verify next, a grep on the deploy log, three verified answers, the stage flag and the next brief](docs/demo/sample-run.gif)

[Watch the full video (MP4)](docs/demo/sample-run.mp4)

## What it is

- **Randomized challenges** — a fresh, seeded set of drills each build. A given `SEED` replays the same set; `make new` picks a new one.
- **Docker or Podman** — engine auto-detected (`podman` if on `PATH`, else `docker`); force either with `ENGINE=podman` / `ENGINE=docker`.
- **Real toolbox practice** — `bash`, `tmux`, `nano`, `less`, `tree`, `grep`, `gawk`, `find`. No `sudo`, no compilers, no root for the trainee.
- **Timing records** — `verify` tracks elapsed time; `make best` lists your local runs, fastest first.

> **Spoiler policy.** This README never gives answers or seed-specific facts. The answer key is generated in the container *build* stage and deleted before the runtime image is assembled — the image holds hashes only.

## Requirements

- Docker (Linux/macOS) or Podman (Linux, rootless OK; v4+), GNU `make` and `python3` on the host.
- A free local port (default `2222`).

## Quick start

```sh
make up          # build image, start container, print seed / port / user / password
make ssh         # log in as the trainee (type the printed password)
```

Inside the session:

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

`make new` tears down and starts a fresh challenge set. Run `make help` for the full target list.

## Day 2 troubleshoot profile

A second profile (`PROFILE=troubleshoot`) reuses this same harness for fourteen diagnostic templates: service and Kubernetes crash loops (`svc-crashloop`, `k8s-crashloop`), disk-space and container-crash triage (`disk-full`, `container-crash`), nginx configuration/ownership checks (`nginx-config`), DNS/endpoint mismatch triage (`dns-mismatch`), SELinux access-denial investigation (`selinux-avc`), process/port collision triage (`port-conflict`), memory-exhaustion/OOM investigation (`oom-kill`), container-image pull failures (`image-pull`), Kubernetes Service-selector mismatch triage (`svc-selector`), Kubernetes OOM/resource-limit investigation (`resource-limits`), TLS certificate-expiry investigation (`tls-expiry`), and downstream-dependency failure triage (`dependency-down`). Alongside `systemctl`, `journalctl`, `ss`, `ps`, `kubectl`, `df`, `du`, `free`, `top`, `docker` and `podman`, they exercise `nginx -t`, `ls -l`, `getenforce`, `ausearch`, `curl`, `dig`, `nslookup`, `ping` and `nc`. The diagnostic commands are mocked: one small compiled binary is symlinked under each command name and prints a fixture, while `ls -l` reads real file metadata. There is no privileged container, live service stack, web server or container runtime behind them.

```sh
make up PROFILE=troubleshoot                         # all fourteen, seeded order
# Or choose an exact subset and order:
make up PROFILE=troubleshoot PICK=disk-full,container-crash
make ssh
```

The briefs, hints, checks, flags, `verify` commands, timing records and `make down` / `make best` work exactly as above — only the drills change. Full notes: [docs/troubleshooting.md](docs/troubleshooting.md).

## Make targets

| Target | What it does |
|---|---|
| `make up [SEED=N] [SET=N] [PORT=N]` | Build the image, start the container, print seed/port/credentials and the stage list. |
| `make ssh` | SSH to `127.0.0.1:$PORT` with host-key checking disabled (throwaway container). |
| `make status` | Container state, credentials path, seed, port and saved records. |
| `make down` | Copy completion records to `state/`, then stop and remove the container. |
| `make new` | `down`, then `up` with a fresh seed. |
| `make best` | Local records, fastest first. |
| `make test` | One up/down cycle, then assert the working tree is clean. |
| `make clean` | Remove the container, image, `.instance/` and `state/`. |

Variables: `ENGINE`, `IMAGE`, `CONTAINER`, `PORT`, `SET` (stages per run; default 8 for terminal and 14 for troubleshoot), `SEED`, `SSH_USER`, `INSTANCE` (credentials dir, default `.instance`) and `STATE` (records dir, default `state/`). Every target honours `ENGINE`, including the recursive `make` calls in `new` and `test`. The demo recorder uses `INSTANCE`/`STATE` to keep its own throwaway files.

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

## Troubleshooting

- **Port in use** — `make up PORT=2223`.
- **Docker permission denied** — add your user to the `docker` group.
- **Podman permission denied** — rootless Podman needs `subuid`/`subgid` ranges for your user; check `podman unshare id`, then add `<user>:100000:65536` to `/etc/subuid` and `/etc/subgid` and run `podman system migrate` once.
- **Podman `unauthorized: incorrect username or password` on pull** — a stale Docker Hub login in `~/.docker/config.json` (Docker silently falls back to anonymous, Podman does not); run `podman login docker.io`, remove that entry, or set `REGISTRY_AUTH_FILE` to an empty auth file.
- **`no container named …` after switching engines** — `make down`/`status`/`clean` only see the engine they talk to; add `ENGINE=docker` or `ENGINE=podman` to match the engine that started the container.
- **Container exits immediately** — `make up` prints the last log lines; the entrypoint refuses to start without `SSH_PASSWORD`.
- **Want the pristine challenge set back after editing** — `make down && make up` rebuilds it (same seed unless you pass a new one).

## More

- Spoiler-free guided walkthrough of every drill: [docs/walkthrough.md](docs/walkthrough.md).
- Re-record the demo (VHS, tapes, fixed seed, GIF/MP4): [docs/demo/README.md](docs/demo/README.md).

## License

MIT — see [LICENSE](LICENSE).
