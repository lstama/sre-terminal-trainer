#!/bin/sh
# sre-terminal-trainer: create the trainee login from SSH_USER/SSH_PASSWORD,
# normalise ownership of the corpus and state, then hand off to sshd.
set -eu

name="${SSH_USER:-trainee}"
: "${SSH_PASSWORD:?SSH_PASSWORD must be set (the container has no default password)}"

grep -q "^${name}:" /etc/group || groupadd -g 1000 "$name"
id -u "$name" >/dev/null 2>&1 || useradd -u 1000 -g "$(grep "^${name}:" /etc/group | cut -d: -f3)" -d /home/trainee -s /bin/bash -M "$name"
printf '%s:%s\n' "$name" "$SSH_PASSWORD" | chpasswd

chown -R 1000:1000 /home/trainee
mkdir -p /run/sshd /var/lib/sre-trainer/state
chown -R 0:0 /var/lib/sre-trainer
find /var/lib/sre-trainer -type d -exec chmod 0700 {} +
find /var/lib/sre-trainer -type f -exec chmod 0600 {} +
chown 0:0 /usr/local/bin/verify
chmod 4755 /usr/local/bin/verify

exec "$@"
