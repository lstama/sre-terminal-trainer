#!/bin/sh
# sre-terminal-trainer: create the trainee login from SSH_USER/SSH_PASSWORD,
# normalise ownership of the corpus and state, then hand off to sshd.
set -eu

name="${SSH_USER:-trainee}"
: "${SSH_PASSWORD:?SSH_PASSWORD must be set (the container has no default password)}"
home=/home/trainee

# Literal (not regex) membership test: SSH_USER is only constrained by the host
# operator, so never interpolate it into a grep pattern.
if ! awk -F: -v n="$name" '$1 == n { found = 1 } END { exit found ? 0 : 1 }' /etc/group; then
  groupadd -g 1000 "$name"
fi
if ! id -u "$name" >/dev/null 2>&1; then
  gid=$(awk -F: -v n="$name" '$1 == n { print $3 }' /etc/group)
  useradd -u 1000 -g "$gid" -d "$home" -s /bin/bash -M "$name"
fi
printf '%s:%s\n' "$name" "$SSH_PASSWORD" | chpasswd

chown -R 1000:1000 "$home"
mkdir -p /run/sshd /var/lib/sre-trainer/state
chown -R 0:0 /var/lib/sre-trainer
find /var/lib/sre-trainer -type d -exec chmod 0700 {} +
find /var/lib/sre-trainer -type f -exec chmod 0600 {} +
chown 0:0 /usr/local/bin/verify
chmod 4755 /usr/local/bin/verify

exec "$@"
