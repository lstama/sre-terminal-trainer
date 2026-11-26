# syntax=docker/dockerfile:1
#
# sre-terminal-trainer
# --------------------
# Three stages: the builder generates the challenge corpus for one seed and
# compiles the static helpers; the troubleshoot runtime adds the mock command
# layer, while the final terminal runtime remains the default image.
#
# Builds unchanged with `docker build` and `podman build` (buildah reads the
# same file and honours .dockerignore; only standard instructions are used, so
# the `syntax=` line above is a no-op under buildah).
#
# The Alpine patch level is pinned on purpose: a rebuild must not silently
# change musl, OpenSSH or coreutils underneath the challenges.
ARG ALPINE_VERSION=3.24.4


# ---------------------------------------------------------------------------
# builder: generate challenges + compile fully static helpers
# ---------------------------------------------------------------------------
FROM alpine:${ALPINE_VERSION} AS builder

# SEED and SET are the only knobs of a build; changing either re-runs the
# generator (and therefore rebuilds the runtime layer that copies its output).
ARG SEED=1
ARG SET=8
# Empty values (including a plain `docker build` with no build args) mean the
# original terminal profile with no --pick override.
ARG PROFILE=terminal
ARG PICK=

RUN apk add --no-cache python3 gcc musl-dev

WORKDIR /build
COPY generator/ ./generator/
COPY src/ ./src/

# Output contract consumed below:
#   /generated/fs/            challenge files at container-absolute paths
#   /generated/manifest       MODE / SEED / STAGES / STAGE lines
#   /generated/stages/NN/     instruction, hint, checks.tsv, flag.sha256
#   /generated/final.sha256   sha256 of the final flag
#   /generated/valid_names.inc builder-only C table for sremock name validation
#   /generated/fixtures/      flat trainee-readable troubleshoot fixtures
# answers.json is test-only plaintext and is deleted before this stage ends,
# so no answer ever reaches the runtime image.
RUN extra=""; \
    if [ -n "${PROFILE}" ] && [ "${PROFILE}" != terminal ]; then \
      extra="--profile ${PROFILE}"; \
      if [ -n "${PICK}" ]; then extra="${extra} --pick ${PICK}"; fi; \
    fi; \
    python3 generator/generate.py \
        --seed "${SEED}" \
        --set "${SET}" \
        --out /generated \
        --dump-answers /generated/answers.json \
        ${extra} \
 && gcc -O2 -static -o /generated/verify src/sha256.c src/util.c src/verify.c \
 && gcc -O2 -static -DSREMOCK_VALID_NAMES=1 -I/generated \
        -o /generated/sremock src/sremock.c \
 && rm -f /generated/answers.json


# ---------------------------------------------------------------------------
# troubleshoot runtime: terminal profile + compiled fixture dispatcher
# ---------------------------------------------------------------------------
FROM alpine:${ALPINE_VERSION} AS troubleshoot

# Keep the terminal toolbox identical; the mocks shadow selected real tools.
# The nginx service account owns generated reference assets so `ls -l` exposes
# real service-user metadata to the nginx-config and perm-denied drills.
RUN apk add --no-cache \
        openssh bash tmux nano grep tree less coreutils diffutils gawk findutils shadow \
    && addgroup -S nginx \
    && adduser -S -D -H -G nginx -s /sbin/nologin nginx

# Explicit ordering is part of the fixture contract: /usr/local/bin mocks must
# win over both BusyBox applets in /bin and package tools in /usr/bin.
ENV PATH="/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"

COPY docker/sshd_config /etc/ssh/sshd_config
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

COPY --from=builder /generated/verify /usr/local/bin/verify
COPY --from=builder /generated/sremock /usr/local/bin/sremock
COPY --from=builder /generated/fixtures/ /var/lib/sremock/current/

# Root-only corpus: 0700 directories, 0600 files (enforced below).
COPY --from=builder /generated/manifest /var/lib/sre-trainer/manifest
COPY --from=builder /generated/stages /var/lib/sre-trainer/stages
COPY --from=builder /generated/final.sha256 /var/lib/sre-trainer/final.sha256

# Challenge files land at their real absolute paths (/home/trainee/...,
# /var/log/sre/...) with the generator's modes preserved: perm drills inspect
# those bits, so no blanket chmod here. Only ownership of the home tree moves
# to the trainee, otherwise a mode-0600 file would be unreadable to them.
COPY --from=builder /generated/fs/ /

RUN chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/sremock \
 && chown 0:0 /usr/local/bin/verify /usr/local/bin/sremock \
 && chmod 4755 /usr/local/bin/verify \
 && for command in systemctl journalctl ps ss curl df free top du nginx \
        getenforce ausearch dig nslookup ping nc docker podman kubectl; do \
        ln -s sremock "/usr/local/bin/${command}"; \
    done \
 && mkdir -p /var/lib/sre-trainer/state /home/trainee /run/sshd \
 && touch /var/lib/sre-trainer/state/records.jsonl \
 && chown -R 0:0 /var/lib/sre-trainer /var/lib/sremock \
 && find /var/lib/sre-trainer -type d -exec chmod 0700 {} + \
 && find /var/lib/sre-trainer -type f -exec chmod 0600 {} + \
 && find /var/lib/sremock -type d -exec chmod 0755 {} + \
 && find /var/lib/sremock -type f -exec chmod 0644 {} + \
 && if [ -d /etc/nginx/references ]; then \
         find /etc/nginx/references -type f -exec chown nginx:nginx {} +; \
     fi \
 && if [ -d /srv/sre-trainer/references ]; then \
         find /srv/sre-trainer/references -type f -exec chown nginx:nginx {} +; \
      fi \
 && chown -R 1000:1000 /home/trainee \
 && ssh-keygen -A

EXPOSE 22

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/sbin/sshd", "-D", "-e"]


# ---------------------------------------------------------------------------
# terminal runtime: sshd + the challenge itself (unchanged default target)
# ---------------------------------------------------------------------------
FROM alpine:${ALPINE_VERSION} AS terminal

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
