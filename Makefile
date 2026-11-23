# sre-terminal-trainer - host-side orchestration.
#
# Local, gitignored artefacts:
#   .instance/creds   ssh user + password for the running container (mode 0600)
#   .instance/seed    seed of the last build
#   state/            completion records copied out of the container by `down`
#
# Requires: docker, GNU make, python3 (host side only: random creds, `best`).

SHELL := /bin/sh
.SHELLFLAGS := -eu -c
.ONESHELL:
.DEFAULT_GOAL := help

IMAGE     ?= sre-terminal-trainer:local
CONTAINER ?= sre-trainer
PORT      ?= 2222
SET       ?= 8
SEED      ?=
SSH_USER  ?= trainee

INSTANCE := .instance
CREDS    := $(INSTANCE)/creds
SEEDFILE := $(INSTANCE)/seed
STATE    := state

RANDOM_PASSWORD = python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(16)))'
RANDOM_SEED     = python3 -c 'import secrets; print(secrets.randbelow(899999)+100000)'
PORT_FREE       = python3 -c 'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); sys.exit(0 if s.connect_ex(("127.0.0.1", $(PORT))) else 1)'

.PHONY: help up down ssh new status best test clean

help:
	@printf '%s\n' \
	  'sre-terminal-trainer' \
	  '' \
	  '  make up [SEED=1234] [SET=8] [PORT=2222]' \
	  '                           build the image and start the container' \
	  '  make ssh                log in as the trainee (password prompt)' \
	  '  make status             container, credentials, seed, stage and record status' \
	  '  make down               save completion records, then remove the container' \
	  '  make new                down + up with a fresh random seed' \
	  '  make best               local completion records, fastest first' \
	  '  make test               up/down cycle, then assert the tree stays clean' \
	  '  make clean              remove container, image, .instance/ and state/' \
	  '' \
	  'Variables: IMAGE, CONTAINER, PORT, SET, SEED, SSH_USER' \
	  '' \
	  'State lives in .instance/ (0600 credentials) and state/ (records).' \
	  'Both are gitignored; only source, config and docs are committed.'

up:
	@mkdir -p $(INSTANCE) $(STATE)
	chmod 700 $(INSTANCE)
	if [ ! -s $(CREDS) ]; then
	  printf 'SSH_USER=%s\nSSH_PASSWORD=%s\n' '$(SSH_USER)' "$$($(RANDOM_PASSWORD))" > $(CREDS)
	  chmod 600 $(CREDS)
	  printf '==> generated new ssh credentials in %s\n' '$(CREDS)'
	fi
	if [ -n '$(SEED)' ]; then printf '%s\n' '$(SEED)' > $(SEEDFILE); fi
	if [ ! -s $(SEEDFILE) ]; then
	  printf '%s\n' "$$($(RANDOM_SEED))" > $(SEEDFILE)
	fi
	seed=$$(cat $(SEEDFILE))
	case "$$seed" in
	  ''|*[!0-9]*) echo "error: seed '$$seed' is not an integer" >&2; exit 1 ;;
	esac
	user=$$(sed -n 's/^SSH_USER=//p' $(CREDS))
	password=$$(sed -n 's/^SSH_PASSWORD=//p' $(CREDS))
	if [ -z "$$user" ] || [ -z "$$password" ]; then
	  echo "error: $(CREDS) must define SSH_USER and SSH_PASSWORD" >&2
	  exit 1
	fi
	if ! $(PORT_FREE); then
	  echo "error: port $(PORT) is already in use; retry with PORT=<free port>" >&2
	  exit 1
	fi
	printf '==> building %s (seed=%s set=%s)\n' '$(IMAGE)' "$$seed" '$(SET)'
	docker build --build-arg SEED="$$seed" --build-arg SET='$(SET)' -t '$(IMAGE)' .
	docker rm -f '$(CONTAINER)' >/dev/null 2>&1 || true
	printf '==> starting %s (port %s)\n' '$(CONTAINER)' '$(PORT)'
	docker run -d --name '$(CONTAINER)' -p '$(PORT)':22 \
	  -e SSH_USER="$$user" -e SSH_PASSWORD="$$password" '$(IMAGE)' >/dev/null
	if ! docker ps --filter 'name=^$(CONTAINER)$$' --format '{{.Names}}' | grep -q .; then
	  echo 'error: the container exited immediately; last log lines:' >&2
	  docker logs '$(CONTAINER)' 2>&1 | tail -n 20 >&2 || true
	  exit 1
	fi
	i=0
	while [ $$i -lt 30 ]; do
	  if python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1',$(PORT)))==0 else 1)"; then break; fi
	  i=$$((i+1)); sleep 0.5
	done
	stages=$$(docker exec '$(CONTAINER)' sh -c 'grep "^STAGE " /var/lib/sre-trainer/manifest | cut -d" " -f2' || true)
	printf '\n  seed     : %s\n  set      : %s\n  port     : %s\n  user     : %s\n  password : %s\n' \
	  "$$seed" '$(SET)' '$(PORT)' "$$user" "$$password"
	printf '\n  stages (%s):\n' "$$(printf "%s\n" "$$stages" | grep -c . || true)"
	printf '%s\n' "$$stages" | sed 's/^/    - /'
	printf '\n  Log in with: make ssh   (or ssh -p %s %s@localhost)\n\n' '$(PORT)' "$$user"

down:
	@mkdir -p $(STATE)
	if docker inspect '$(CONTAINER)' >/dev/null 2>&1; then
	  if docker cp '$(CONTAINER)':/var/lib/sre-trainer/state/records.jsonl $(STATE)/records.jsonl 2>/dev/null; then
	    printf '==> saved %s/records.jsonl\n' '$(STATE)'
	  else
	    printf '==> no completion records in the container to save\n'
	  fi
	  docker rm -f '$(CONTAINER)' >/dev/null
	  printf '==> removed container %s\n' '$(CONTAINER)'
	else
	  printf '==> no container named %s\n' '$(CONTAINER)'
	fi

ssh:
	@if [ ! -s $(CREDS) ]; then echo 'error: no credentials; run make up first' >&2; exit 1; fi
	user=$$(sed -n 's/^SSH_USER=//p' $(CREDS))
	printf '==> ssh -p %s %s@localhost (type the password from make up)\n' '$(PORT)' "$$user"
	ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
	  -p '$(PORT)' "$$user@localhost"
	printf '==> session ended; make down saves the records\n'

new:
	@$(MAKE) --no-print-directory down
	seed=$$($(RANDOM_SEED))
	printf '==> fresh seed %s\n' "$$seed"
	$(MAKE) --no-print-directory up SEED="$$seed"

status:
	@docker ps -a --filter 'name=^$(CONTAINER)$$' --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true
	printf '\n'
	if [ -s $(CREDS) ]; then
	  printf 'credentials : %s (mode %s)\n' '$(CREDS)' "$$(stat -c %a $(CREDS))"
	  printf 'ssh user    : %s\n' "$$(sed -n 's/^SSH_USER=//p' $(CREDS))"
	else
	  printf 'credentials : none (run make up)\n'
	fi
	if [ -s $(SEEDFILE) ]; then printf 'seed        : %s\n' "$$(cat $(SEEDFILE))"; fi
	printf 'port        : %s\n' '$(PORT)'
	if docker inspect '$(CONTAINER)' >/dev/null 2>&1; then
	  printf 'image       : %s\n' "$$(docker inspect --format '{{.Config.Image}}' '$(CONTAINER)')"
	  docker exec '$(CONTAINER)' sh -c 'sed -n "s/^STAGE /  stage     : /p" /var/lib/sre-trainer/manifest' || true
	else
	  printf 'container   : not created\n'
	fi
	if [ -s $(STATE)/records.jsonl ]; then
	  printf 'records     : %s/records.jsonl (%s run(s))\n' '$(STATE)' "$$(grep -c . $(STATE)/records.jsonl || true)"
	else
	  printf 'records     : none yet (make down saves them)\n'
	fi

best:
	@if [ ! -f $(STATE)/records.jsonl ]; then
	  printf 'no local records yet (%s/records.jsonl); run make down after a session\n' '$(STATE)'
	  exit 0
	fi
	if [ ! -s $(STATE)/records.jsonl ]; then
	  printf '%s/records.jsonl is empty: no run has reached the final flag yet\n' '$(STATE)'
	  exit 0
	fi
	python3 tools/best.py $(STATE)/records.jsonl

test:
	@printf '==> up/down cycle\n'
	$(MAKE) --no-print-directory up
	$(MAKE) --no-print-directory down
	if docker inspect '$(CONTAINER)' >/dev/null 2>&1; then
	  echo 'FAIL: container still exists after make down' >&2
	  exit 1
	fi
	if ! docker image inspect '$(IMAGE)' >/dev/null 2>&1; then
	  echo 'FAIL: image was not built' >&2
	  exit 1
	fi
	if [ -n "$$(git status --porcelain)" ]; then
	  printf 'FAIL: working tree not clean after up/down:\n%s\n' "$$(git status --porcelain)" >&2
	  exit 1
	fi
	printf '==> OK: image builds, container runs, tree is clean\n'

clean:
	@docker rm -f '$(CONTAINER)' >/dev/null 2>&1 || true
	docker rmi -f '$(IMAGE)' >/dev/null 2>&1 || true
	rm -rf $(INSTANCE) $(STATE)
	printf '==> removed container, image, %s/ and %s/\n' '$(INSTANCE)' '$(STATE)'
