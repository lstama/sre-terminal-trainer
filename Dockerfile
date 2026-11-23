# syntax=docker/dockerfile:1
#
# sre-terminal-trainer
# --------------------
# Two stages: the builder generates the challenge corpus for one seed and
# compiles the verifier; the runtime image ships only the generated data,
# the setuid verifier, sshd and a small terminal toolbox.
#
# Builds unchanged with `docker build` and `podman build` (buildah reads the
# same file and honours .dockerignore; only standard instructions are used, so
# the `syntax=` line above is a no-op under buildah).
#
# The Alpine patch level is pinned on purpose: a rebuild must not silently
# change musl, OpenSSH or coreutils underneath the challenges.
ARG ALPINE_VERSION=3.24.4


# ---------------------------------------------------------------------------
# builder: generate challenges + compile a fully static verifier
# ---------------------------------------------------------------------------
FROM alpine:${ALPINE_VERSION} AS builder

# SEED and SET are the only knobs of a build; changing either re-runs the
# generator (and therefore rebuilds the runtime layer that copies its output).
ARG SEED=1
ARG SET=8

RUN apk add --no-cache python3 gcc musl-dev

WORKDIR /build
COPY generator/ ./generator/
COPY src/ ./src/

# Output contract consumed below:
#   /generated/fs/            challenge files at container-absolute paths
#   /generated/manifest       SEED / STAGES / STAGE lines
#   /generated/stages/NN/     instruction, hint, checks.tsv, flag.sha256
#   /generated/final.sha256   sha256 of the final flag
# answers.json is test-only plaintext and is deleted before this stage ends,
# so no answer ever reaches the runtime image.
RUN python3 generator/generate.py \
        --seed "${SEED}" \
        --set "${SET}" \
        --out /generated \
        --dump-answers /generated/answers.json \
 && gcc -O2 -static -o /generated/verify src/*.c \
 && rm -f /generated/answers.json


# ---------------------------------------------------------------------------
# runtime: sshd + the challenge itself
# ---------------------------------------------------------------------------
FROM alpine:${ALPINE_VERSION}

# Exactly the toolbox the drills assume; no sudo, no build tools.
RUN apk add --no-cache \
        openssh bash tmux nano grep tree less coreutils diffutils gawk findutils shadow

COPY docker/sshd_config /etc/ssh/sshd_config
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# Setuid so the trainee can open root-only stage data and append root-only
# progress; verify drops privileges while it handles untrusted answers.
COPY --from=builder /generated/verify /usr/local/bin/verify

# Root-only corpus: 0700 directories, 0600 files (enforced below).
COPY --from=builder /generated/manifest /var/lib/sre-trainer/manifest
COPY --from=builder /generated/stages /var/lib/sre-trainer/stages
COPY --from=builder /generated/final.sha256 /var/lib/sre-trainer/final.sha256

# Challenge files land at their real absolute paths (/home/trainee/...,
# /var/log/sre/...) with the generator's modes preserved: perm drills inspect
# those bits, so no blanket chmod here. Only ownership of the home tree moves
# to the trainee, otherwise a mode-0600 file would be unreadable to them.
COPY --from=builder /generated/fs/ /

RUN chmod 0755 /usr/local/bin/entrypoint.sh \
 && chown 0:0 /usr/local/bin/verify && chmod 4755 /usr/local/bin/verify \
 && mkdir -p /var/lib/sre-trainer/state /home/trainee /run/sshd \
 && touch /var/lib/sre-trainer/state/records.jsonl \
 && chown -R 0:0 /var/lib/sre-trainer \
 && find /var/lib/sre-trainer -type d -exec chmod 0700 {} + \
 && find /var/lib/sre-trainer -type f -exec chmod 0600 {} + \
 && chown -R 1000:1000 /home/trainee \
 && ssh-keygen -A

EXPOSE 22

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/sbin/sshd", "-D", "-e"]
