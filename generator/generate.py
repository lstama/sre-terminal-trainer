#!/usr/bin/env python3
"""Deterministic, randomized sysadmin terminal-challenge generator.

Python 3 standard library only.  Everything written by this script lives under
the directory passed with --out (plus the optional --dump-answers file).

CLI:
    python3 generate.py --seed SEED --set N --out DIR [--dump-answers FILE] \\
        [--profile terminal|troubleshoot] [--pick id1,id2]
"""

import argparse
import hashlib
import json
import os
import random
import sys

# ---------------------------------------------------------------------------
# constants / small helpers
# ---------------------------------------------------------------------------

HEX = "0123456789abcdef"
LEVELS = ["INFO", "WARN", "ERROR", "DEBUG", "NOTICE"]
LEVEL_WEIGHTS = [62, 20, 12, 4, 2]
SERVICES = [
    "nginx", "sshd", "systemd", "kubelet", "dockerd", "postgres", "redis",
    "cron", "kernel", "uwsgi", "haproxy", "prometheus", "etcd", "containerd",
]
PODS = ["api-7d9f4b", "worker-5c2a1e", "web-84b6c7", "cache-3f1d0a", "proxy-9e2b5d"]
MSG_TPLS = [
    "connection from {ip} port {port} accepted",
    "request GET /api/v1/orders completed in {ms}ms status 200",
    "health probe succeeded for pod {pod}",
    "configuration reloaded from disk",
    "cache miss for key session:{hex}",
    "TLS handshake completed with {ip}",
    "background job job-{n} finished in {ms}ms",
    "disk usage on /var at {pct}%",
    "token refresh scheduled in {n}s",
    "worker {n} picked up task {hex}",
    "upstream {ip}:8080 response time {ms}ms",
    "session {hex} authenticated for user svc-{n}",
    "retry {n} for backend {pod} due to timeout",
    "metrics scrape from {ip} completed",
]

ALL_IDS = [
    "deploy-error",
    "top-talker",
    "conf-hunt",
    "crlf-fix",
    "line-surgery",
    "split-incident",
    "window-fragments",
    "copy-mode",
    "log-growth",
    "config-drift",
    "live-tail",
    "perm-audit",
    "noise-filter",
    "nano-replace",
    "cut-paste",
    "nav-maze",
]


def sha_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def tok(rng, prefix, n=10):
    """Random, human-readable token built only from the deterministic RNG."""
    return prefix + "-" + "".join(rng.choice(HEX) for _ in range(n))


def ip(rng):
    return "10.%d.%d.%d" % (rng.randint(0, 40), rng.randint(0, 255), rng.randint(1, 254))


def ts(rng):
    return "2026-11-%02dT%02d:%02d:%02dZ" % (
        rng.randint(1, 23), rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)
    )


def access_ts(rng):
    return "23/Nov/2026:%02d:%02d:%02d +0000" % (
        rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)
    )


def sys_line(rng):
    svc = rng.choice(SERVICES)
    level = rng.choices(LEVELS, weights=LEVEL_WEIGHTS, k=1)[0]
    msg = rng.choice(MSG_TPLS).format(
        ip=ip(rng), port=rng.randint(1024, 65535), ms=rng.randint(2, 900),
        pod=rng.choice(PODS), hex=tok(rng, "h", 6), pct=rng.randint(20, 95),
        n=rng.randint(1, 9999),
    )
    return "%s %s[%d]: %s %s" % (ts(rng), svc, rng.randint(100, 9999), level, msg)


def cfg_line(rng, i):
    key = rng.choice([
        "worker_connections", "keepalive_timeout", "upstream_timeout",
        "max_requests", "buffer_size", "log_level", "retry_count",
        "pool_size", "idle_timeout", "max_retries",
    ])
    return "%s = %d  # section %05d generated" % (key, rng.randint(1, 65535), i)


def access_line(rng, client, frag=None):
    path = rng.choice([
        "/api/v1/orders", "/healthz", "/metrics", "/login",
        "/static/app.js", "/api/v2/search?q=widgets",
    ])
    method = rng.choice(["GET", "GET", "GET", "POST", "PUT"])
    status = rng.choice([200, 200, 200, 200, 201, 304, 404, 500, 502])
    size = rng.randint(180, 9000)
    ua = rng.choice(["curl/8.5.0", "Mozilla/5.0", "python-requests/2.31", "kube-probe/1.29"])
    suffix = (" sess=" + frag) if frag else ""
    return '%s - - [%s] "%s %s HTTP/1.1" %d %d "-" "%s%s"' % (
        client, access_ts(rng), method, path, status, size, ua, suffix
    )


def wfile(fsroot, abs_path, content, mode=None):
    """Write a trainee-visible file; abs_path is a container-absolute path."""
    if not abs_path.startswith("/"):
        raise ValueError("absolute container path required: %r" % abs_path)
    full = os.path.join(fsroot, abs_path.lstrip("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    # A previous run may have left a read-only file at this path.
    if os.path.exists(full):
        os.chmod(full, 0o600)
    with open(full, "wb") as fh:
        fh.write(data)
    if mode is not None:
        os.chmod(full, mode)
    return full


def wdir(fsroot, abs_path):
    full = os.path.join(fsroot, abs_path.lstrip("/"))
    os.makedirs(full, exist_ok=True)
    return full


def hash_check(cid, fragment, hint, plain=None):
    plain = fragment if plain is None else plain
    return {
        "id": cid, "type": "hash", "answer": sha_text(plain),
        "fragment": fragment, "hint": hint, "plain": plain,
    }


def file_check(cid, path, content, fragment, hint, initial=None):
    """Build a file check whose sha256 is the EXPECTED CORRECTED bytes.

    ``content`` is the corrected end state (kept in memory only, never written into
    fs/).  ``initial`` is the broken bytes actually shipped in fs/; when supplied we
    cross-foot that the challenge is not already solved.
    """
    data = content.encode("utf-8") if isinstance(content, str) else content
    if initial is not None:
        init_data = initial.encode("utf-8") if isinstance(initial, str) else initial
        if sha_bytes(init_data) == sha_bytes(data):
            raise ValueError("file check %s: initial file already matches expected content" % cid)
    return {
        "id": cid, "type": "file", "path": path, "sha256": sha_bytes(data),
        "fragment": fragment, "hint": hint, "plain": content, "expected": content,
    }


# submit footer shared by every stage
def with_submit(instruction, checks):
    lines = [instruction.rstrip(), "", "Checks and submission:"]
    for c in checks:
        lines.append("  %s - %s" % (c["id"], c["hint"]))
        if c["type"] == "hash":
            lines.append("  Submit with: verify check %s <answer>" % c["id"])
        else:
            lines.append("  Submit with: verify check %s <file at %s>" % (c["id"], c["path"]))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 16 challenge templates
# ---------------------------------------------------------------------------

TEMPLATES = {}


def template(tid):
    def deco(fn):
        TEMPLATES[tid] = fn
        return fn
    return deco


@template("deploy-error")
def t_deploy_error(rng, fs):
    base = "/var/log/sre/deploy-" + tok(rng, "d", 8)
    logp = base + "/deploy.log"
    n = rng.randint(900, 1400)
    bad = rng.randint(120, n - 120)
    frag1, frag2 = tok(rng, "rel"), tok(rng, "rbk")
    errcount = rng.randint(5, 11)
    choices = [i for i in range(1, n + 1) if i not in (bad, bad + 2)]
    errs = set(rng.sample(choices, errcount))
    lines = []
    for i in range(1, n + 1):
        if i == bad:
            lines.append("%s deploy[4210]: ERROR rollout failed for release %s at stage canary; aborting" % (ts(rng), frag1))
        elif i == bad + 2:
            lines.append("%s deploy[4210]: WARN automatic rollback initiated, restoring manifest %s" % (ts(rng), frag2))
        elif i in errs:
            lines.append("%s deploy[4210]: ERROR deploy step failed for pod %s" % (ts(rng), rng.choice(PODS)))
        else:
            lines.append(sys_line(rng))
    wfile(fs, logp, "\n".join(lines) + "\n")
    checks = [
        hash_check("deploy-error-1", frag1, "Find the failed release id in the deploy log and submit it."),
        hash_check("deploy-error-2", frag2, "Find the rollback manifest id named two lines after the failure and submit it."),
        hash_check("deploy-error-3", str(errcount), "Count the ERROR deploy lines in the log and submit the number.", plain=str(errcount)),
    ]
    instr = (
        "A production deployment failed. The deploy log at %s holds the release id, "
        "the rollback manifest id and the number of ERROR deploy lines. Inspect the log "
        "with grep and submit each answer." % logp
    )
    hints = [
        "Tier 1: grep -rniE 'rollout failed|ERROR deploy' <log> searches recursively, ignoring case, with extended regex.",
        "Tier 2: grep -B3 -A3 'rollout failed' <log> shows three lines of context before and after the match.",
        "Tier 3: grep -c 'ERROR deploy' <log> prints only the count of matching lines.",
    ]
    return instr, hints, checks


@template("top-talker")
def t_top_talker(rng, fs):
    logp = "/var/log/sre/access-" + tok(rng, "a", 8) + ".log"
    total = 20000
    topn = rng.randint(1500, 1900)
    topip = ip(rng)
    # Decoy clients: distinct IPs, each with strictly fewer requests than the
    # top talker.  The drill is "find the IP with the most requests", so the
    # invariant below is load-bearing: it is re-derived from the written log
    # before the answers are accepted as correct.
    seen = {topip}
    others = []
    n_others = rng.randint(30, 45)
    while len(others) < n_others:
        cand = ip(rng)
        if cand in seen:
            continue
        seen.add(cand)
        others.append(cand)
    rest = total - topn
    base, extra = divmod(rest, len(others))
    counts = [base + (1 if i < extra else 0) for i in range(len(others))]
    rng.shuffle(counts)
    if max(counts) >= topn:
        raise AssertionError(
            "top-talker: decoy count %d would tie/beat the top talker %d"
            % (max(counts), topn))
    rows = [topip] * topn
    for client, n in zip(others, counts):
        rows.extend([client] * n)
    rng.shuffle(rows)
    wfile(fs, logp, "\n".join(access_line(rng, c) for c in rows) + "\n")

    # Self-check: recompute the winner from the log bytes on disk and compare
    # it with the answers below, so a generated drill can never disagree with
    # the file the trainee is told to analyse.
    counted = {}
    with open(os.path.join(fs, logp.lstrip("/")), "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                client = line.split()[0]
                counted[client] = counted.get(client, 0) + 1
    winner = max(counted.items(), key=lambda kv: kv[1])
    if len(counted) != len(others) + 1:
        raise AssertionError("top-talker: expected %d clients, log has %d"
                             % (len(others) + 1, len(counted)))
    if winner != (topip, topn):
        raise AssertionError("top-talker: log winner %r != stored answer %r"
                             % (winner, (topip, topn)))
    if sum(counted.values()) != total:
        raise AssertionError("top-talker: log has %d lines, expected %d"
                             % (sum(counted.values()), total))
    checks = [
        hash_check("top-talker-1", topip, "Identify the source IP with the most requests and submit it."),
        hash_check("top-talker-2", str(topn), "Count requests from that top talker IP and submit the number.", plain=str(topn)),
    ]
    instr = (
        "The web access log at %s has one client making far more requests than anyone "
        "else. Use awk, sort, uniq and head to find that top talker, then count its "
        "requests." % logp
    )
    hints = [
        "Tier 1: awk '{print $1}' <log> | sort | uniq -c | sort -nr | head lists IPs by request count.",
        "Tier 2: sort -nr sorts the counted lines numerically in descending order; head -n 1 keeps the winner.",
        "Tier 3: grep -c '<ip>' <log> counts how many lines contain that IP.",
    ]
    return instr, hints, checks


@template("conf-hunt")
def t_conf_hunt(rng, fs):
    root = "/home/trainee/conf-" + tok(rng, "c", 8)
    parts = ["srv", "etc", "opt", "apps", "svc", "config", "deploy", "node", "env", "region"]
    depth = rng.randint(5, 8)
    segs = [rng.choice(parts) for _ in range(depth)]
    roguedir = root + "/" + "/".join(segs)
    rogue = roguedir + "/rogue-" + tok(rng, "r", 6) + ".conf"
    frag1, frag2 = tok(rng, "rogue"), tok(rng, "owner")
    # decoy configs
    for k in range(26):
        d = root + "/" + "/".join(rng.choice(parts) for _ in range(rng.randint(1, depth)))
        p = d + "/app-" + tok(rng, "x", 5) + ".conf"
        wfile(fs, p, "\n".join(cfg_line(rng, i) for i in range(rng.randint(8, 20))) + "\n")
    body = ["# rogue configuration discovered during audit"]
    body += [cfg_line(rng, i) for i in range(6)]
    body.append("rogue_id = %s" % frag1)
    body += [cfg_line(rng, i) for i in range(6, 10)]
    body.append("owner_ref = %s" % frag2)
    wfile(fs, rogue, "\n".join(body) + "\n")
    checks = [
        hash_check("conf-hunt-1", frag1, "Find the rogue .conf file and submit its rogue_id value."),
        hash_check("conf-hunt-2", frag2, "Submit the owner_ref value stored in that same rogue config."),
    ]
    instr = (
        "A rogue .conf file was dropped somewhere under %s. Locate it, then submit "
        "the rogue_id and owner_ref values it contains." % root
    )
    hints = [
        "Tier 1: find %s -name '*.conf' lists every config file under the tree." % root,
        "Tier 2: tree -L 2 <dir> shows a shallow directory overview; cd - returns to the previous directory and pwd prints where you are.",
        "Tier 3: grep -rn 'rogue_id' %s searches file contents recursively with line numbers." % root,
    ]
    return instr, hints, checks


@template("crlf-fix")
def t_crlf_fix(rng, fs):
    root = "/home/trainee/crlf-" + tok(rng, "f", 8)
    cfg = root + "/app.cfg"              # checked path: ships BROKEN (CRLF)
    frag1, frag2 = tok(rng, "leg"), tok(rng, "maint")
    lines = []
    for i in range(60):
        if i == 17:
            lines.append("legacy_token = %s" % frag1)
        elif i == 41:
            lines.append("# maintained-by %s" % frag2)
        else:
            lines.append(cfg_line(rng, i))
    lf = "\n".join(lines) + "\n"          # expected corrected bytes, in memory only
    broken = lf.replace("\n", "\r\n")
    wfile(fs, cfg, broken)
    checks = [
        file_check("crlf-fix-1", cfg, lf, frag1,
                   "Convert the config in place to plain LF line endings.", initial=broken),
        hash_check("crlf-fix-2", frag2, "Submit the maintainer token found in a comment of that config."),
    ]
    instr = (
        "The config %s was saved with CRLF line endings and tools misparse it. The corrected "
        "end state is byte-for-byte the same 60 lines with every line ending changed from "
        "CRLF to plain LF (a single \\n byte, no carriage return); no line content may change, "
        "including the legacy_token line and the maintainer comment. Inspect with cat -A, "
        "convert with a nano search/replace or dos2unix, save in place, and report the "
        "maintainer token found in its comments." % cfg
    )
    hints = [
        "Tier 1: cat -A <file> shows line endings: CRLF appears as ^M$ while LF is just $.",
        "Tier 2: file <file> reports 'with CRLF line terminators'; stat <file> shows size and timestamps.",
        "Tier 3: in nano, Ctrl+\\ starts an interactive search-and-replace; search for the carriage return and replace with nothing.",
    ]
    return instr, hints, checks


@template("line-surgery")
def t_line_surgery(rng, fs):
    root = "/home/trainee/linesurgery-" + tok(rng, "l", 8)
    cfg = root + "/app.conf"            # checked path: ships BROKEN
    n = 20000
    bad = rng.randint(3000, n - 3000)
    frag1 = tok(rng, "fix")
    lines = [cfg_line(rng, i) for i in range(n)]
    fixed_line = "worker_timeout = %s  # fixed line" % frag1
    good_lines = list(lines)
    good_lines[bad - 1] = fixed_line
    broken_lines = list(lines)
    broken_lines[bad - 1] = "BROKEN LINE -- corrupt value, replace me"
    good_content = "\n".join(good_lines) + "\n"
    broken_content = "\n".join(broken_lines) + "\n"
    wfile(fs, cfg, broken_content)
    checks = [
        file_check("line-surgery-1", cfg, good_content, frag1,
                   "Replace the single corrupt line with the documented correct line.", initial=broken_content),
        hash_check("line-surgery-2", str(bad), "Find the 1-based line number of the broken line and submit it.", plain=str(bad)),
    ]
    instr = (
        "A 20,000-line config at %s has exactly one corrupt line containing 'BROKEN LINE'. "
        "The corrected end state is that entire line replaced by exactly: %s Every one of "
        "the other 19,999 lines must remain byte-for-byte unchanged. Use grep -n to locate "
        "the damage, then nano (Ctrl+_ to jump to a line number, Ctrl+O to save, Ctrl+X to "
        "exit) to write the replacement in place." % (cfg, fixed_line)
    )
    hints = [
        "Tier 1: grep -n 'BROKEN LINE' <file> prints the matching line with its number.",
        "Tier 2: in nano, Ctrl+_ prompts for a line number to jump to; Ctrl+O writes and Ctrl+X exits.",
        "Tier 3: sed -n '<n>p' <file> prints just line n if you need to confirm the target line.",
    ]
    return instr, hints, checks


@template("split-incident")
def t_split_incident(rng, fs):
    base = "/var/log/sre/incident-" + tok(rng, "i", 8)
    logp = base + "/events.log"
    cfgroot = "/home/trainee/incident-" + tok(rng, "j", 8)
    cfg = cfgroot + "/tracker.conf"
    n = 4200
    at = rng.randint(1800, n - 400)
    frag1, frag2 = tok(rng, "inc"), tok(rng, "comp")
    lines = []
    for i in range(1, n + 1):
        if i == at:
            lines.append("%s kubelet[1180]: ERROR incident id=%s severity=sev2" % (ts(rng), frag1))
        elif i == at + 7:
            lines.append("%s dockerd[1181]: WARN affected component=%s restarting" % (ts(rng), frag2))
        else:
            lines.append(sys_line(rng))
    wfile(fs, logp, "\n".join(lines) + "\n")
    cfg_lines = [
        "# incident tracker", "enabled = true",
        "incident_id = PENDING", "poll_interval = 30", "escalate = true",
    ]
    broken = "\n".join(cfg_lines) + "\n"
    fixed = "\n".join("incident_id = %s" % frag1 if x == "incident_id = PENDING" else x for x in cfg_lines) + "\n"
    wfile(fs, cfg, broken)              # checked path: ships BROKEN (PENDING)
    checks = [
        file_check("split-incident-1", cfg, fixed, frag1,
                   "Replace the PENDING placeholder with the incident id from the log.", initial=broken),
        hash_check("split-incident-2", frag2, "Submit the affected component token logged a few lines after the incident."),
    ]
    instr = (
        "An incident is unfolding. Cross-reference the long log %s while repairing the "
        "tracker config %s (which currently ships with `incident_id = PENDING`). Find the "
        "incident id in the log and replace that entire line in place with "
        "`incident_id = <the id you found>`; every other line must stay byte-for-byte "
        "identical. Also report the affected component token. A tmux split pane makes "
        "side-by-side work easier." % (logp, cfg)
    )
    hints = [
        "Tier 1: grep -n 'incident id' <log> pinpoints the incident line; Ctrl+b % splits a tmux pane vertically.",
        "Tier 2: Ctrl+b o switches between panes; in nano Ctrl+w searches and Alt+w repeats the last search.",
        "Tier 3: grep -A7 'incident id' <log> shows seven following lines, including the component entry.",
    ]
    return instr, hints, checks


@template("window-fragments")
def t_window_fragments(rng, fs):
    root = "/home/trainee/windows-" + tok(rng, "w", 8)
    frag = [tok(rng, "w%d" % k) for k in range(3)]
    for k in range(3):
        d = root + "/w%d" % (k + 1)
        lines = []
        for i in range(300):
            if i == 137 + k * 11:
                lines.append("%s collector[2%d0]: marker=%s" % (ts(rng), k, frag[k]))
            else:
                lines.append(sys_line(rng))
        wfile(fs, d + "/segment-%d.log" % (k + 1), "\n".join(lines) + "\n")
    checks = [
        hash_check("window-fragments-1", frag[0], "Submit the marker found in directory w1."),
        hash_check("window-fragments-2", frag[1], "Submit the marker found in directory w2."),
        hash_check("window-fragments-3", frag[2], "Submit the marker found in directory w3."),
    ]
    instr = (
        "Three markers are scattered in three separate log segments under %s. Open one "
        "tmux window per segment (Ctrl+b c creates one, Ctrl+b n/p moves between them), "
        "find each marker and submit all three." % root
    )
    hints = [
        "Tier 1: Ctrl+b c creates a new tmux window; Ctrl+b n and Ctrl+b p move to the next and previous window.",
        "Tier 2: grep -v 'INFO' <log> hides informational noise so markers stand out.",
        "Tier 3: grep -c 'marker=' <log> confirms exactly one marker per segment.",
    ]
    return instr, hints, checks


@template("copy-mode")
def t_copy_mode(rng, fs):
    logp = "/var/log/sre/rolling-" + tok(rng, "m", 8) + ".log"
    n = 100000
    p1 = rng.randint(n - 4000, n - 2500)
    p2 = rng.randint(n - 900, n - 200)
    frag1, frag2 = tok(rng, "rel"), tok(rng, "sig")
    short = ["cron[221]: session refresh ok", "nginx[88]: GET /healthz 200",
             "sshd[41]: accepted publickey", "systemd[1]: unit reported status"]
    lines = []
    for i in range(1, n + 1):
        if i == p1:
            lines.append("%s deploy[600]: RELEASE_MARKER %s" % (ts(rng), frag1))
        elif i == p2:
            lines.append("%s deploy[600]: SIGNATURE %s" % (ts(rng), frag2))
        else:
            lines.append("%s %s" % (ts(rng), rng.choice(short)))
    wfile(fs, logp, "\n".join(lines) + "\n")
    checks = [
        hash_check("copy-mode-1", frag1, "Find the release marker buried near the end of the log and submit it."),
        hash_check("copy-mode-2", frag2, "Find the signature marker even closer to the end and submit it."),
    ]
    instr = (
        "A 100,000-line rolling log at %s contains two markers close to its end. Scrolling "
        "with the mouse is impractical: use tmux copy mode (Ctrl+b [ then End) or tail to "
        "reach the tail of the file, then submit both markers." % logp
    )
    hints = [
        "Tier 1: Ctrl+b [ enters tmux copy mode; pressing End jumps to the end of the captured output.",
        "Tier 2: tail -n 200 <log> prints only the last 200 lines; less +F follows a file as it grows.",
        "Tier 3: grep -n 'MARKER\\|SIGNATURE' <log> locates both markers without scrolling.",
    ]
    return instr, hints, checks


@template("log-growth")
def t_log_growth(rng, fs):
    root = "/var/log/sre/growth-" + tok(rng, "g", 8)
    # Distinct sizes drawn per seed so both the winning filename and the line
    # count answer change between runs.
    sizes = rng.sample(range(1200, 9900, 100), 4)
    rng.shuffle(sizes)
    frag = tok(rng, "big")
    paths = []
    biggest_idx = sizes.index(max(sizes))
    for k, sz in enumerate(sizes):
        p = "%s/service-%d.log" % (root, k + 1)
        lines = [sys_line(rng) for _ in range(sz)]
        if k == biggest_idx:
            lines[sz // 2] = "%s worker[9]: BULK_IMPORT token=%s completed" % (ts(rng), frag)
        wfile(fs, p, "\n".join(lines) + "\n")
        paths.append(p)
    bigp = paths[biggest_idx]
    bigcount = max(sizes)
    checks = [
        hash_check("log-growth-1", frag, "Find the bulk-import token in the largest log and submit it."),
        hash_check("log-growth-2", str(bigcount), "Report the line count of the largest log as a number.", plain=str(bigcount)),
    ]
    instr = (
        "Several service logs were left in %s. One is growing much faster than the others. "
        "Use ls -lah and wc -l to identify it, use head to inspect it, then submit its "
        "bulk-import token and line count." % root
    )
    hints = [
        "Tier 1: ls -lah <dir> sorts by nothing by default but shows human-readable sizes; wc -l <files> counts lines.",
        "Tier 2: du -ah <dir> | sort -h lists files by size so the largest floats to the bottom.",
        "Tier 3: head -n 5 <file> previews the beginning once you know which file is biggest.",
    ]
    return instr, hints, checks


@template("config-drift")
def t_config_drift(rng, fs):
    root = "/home/trainee/drift-" + tok(rng, "d", 8)
    cfg = root + "/app.conf"
    bak = root + "/app.conf.bak"
    frag1, frag2 = tok(rng, "drift"), tok(rng, "base")
    base_lines = [
        "# application configuration", "bind_port = 8080",
        "pool_size = 24", "upstream_host = %s" % frag1,
        "retry_count = 3", "log_level = warn",
    ]
    fixed = "\n".join(base_lines) + "\n"       # expected corrected bytes, in memory only
    drift_lines = list(base_lines)
    drift_lines[3] = "upstream_host = old.internal.example"
    broken = "\n".join(drift_lines) + "\n"
    wfile(fs, cfg, broken)                      # checked path: ships BROKEN (drifted)
    # The backup is a pristine snapshot: correct host plus a baseline reference
    # token that the shipped app.conf does not carry, so "only in the backup"
    # is literally true (asserted below).  Its header differs, so the exact
    # expected bytes are never shipped as any fs file either.
    bak_lines = list(base_lines)
    bak_lines[0] = "# application configuration (backup snapshot)"
    bak_lines.insert(5, "# baseline-ref %s" % frag2)
    bak_content = "\n".join(bak_lines) + "\n"
    wfile(fs, bak, bak_content)
    if frag2 in broken or frag2 not in bak_content:
        raise AssertionError("config-drift: baseline token must be unique to the backup")
    if bak_content == fixed:
        raise AssertionError("config-drift: backup must not equal the expected app.conf")
    checks = [
        file_check("config-drift-1", cfg, fixed, frag1,
                   "Restore the drifted upstream_host line in app.conf from the backup.", initial=broken),
        hash_check("config-drift-2", frag2, "Submit the baseline reference token, which is kept only in the backup."),
    ]
    instr = (
        "The config %s drifted from the reference backup %s. Run diff on the two files. "
        "The corrected end state for %s is: keep its own `# application configuration` "
        "header and every other line unchanged, and replace only the drifted "
        "upstream_host line with the value shown in the backup (`upstream_host = %s`). "
        "Do not copy the backup's header or its extra baseline-ref line. Report the "
        "baseline reference token kept only in the backup too." % (cfg, bak, cfg, frag1)
    )
    hints = [
        "Tier 1: diff <broken> <backup> prints only the differing lines with < and > markers.",
        "Tier 2: nano <file> lets you edit in place; Ctrl+O saves and Ctrl+X exits.",
        "Tier 3: stat <file> shows mtime/size, useful to confirm the backup is the older, pristine copy.",
    ]
    return instr, hints, checks


@template("live-tail")
def t_live_tail(rng, fs):
    logp = "/var/log/sre/live-" + tok(rng, "t", 8) + ".log"
    n = 6000
    frag1, frag2 = tok(rng, "fin"), tok(rng, "pre")
    lines = [sys_line(rng) for _ in range(n - 2)]
    lines.append("%s genserver[3]: PRE_FLIGHT %s" % (ts(rng), frag2))
    lines.append("%s genserver[3]: FINAL_MARKER %s" % (ts(rng), frag1))
    wfile(fs, logp, "\n".join(lines) + "\n")
    checks = [
        hash_check("live-tail-1", frag1, "Submit the final marker at the very end of the rolling log."),
        hash_check("live-tail-2", frag2, "Submit the pre-flight marker on the line before it."),
    ]
    instr = (
        "A live service keeps appending to %s. The final status marker is at the very end "
        "of the stream. Use tail -f or less +F to follow it (and head to peek at the start), "
        "then submit the final and pre-flight markers." % logp
    )
    hints = [
        "Tier 1: tail -f <log> follows new lines as they are appended; press Ctrl+C to stop.",
        "Tier 2: less +F <log> enters follow mode; Ctrl+C returns to normal less navigation.",
        "Tier 3: tail -n 2 <log> prints only the last two lines when following is unnecessary.",
    ]
    return instr, hints, checks


@template("perm-audit")
def t_perm_audit(rng, fs):
    root = "/home/trainee/perm-" + tok(rng, "p", 8)
    frag1, frag2 = tok(rng, "cred"), tok(rng, "key")
    cred_name = rng.choice(["secrets.env", "vault.env", "svc-token.env",
                            "auth.env", "api-keys.env", "credentials.cfg"])
    specs = [
        ("public.txt", 0o644), ("run.sh", 0o755), ("group.cfg", 0o640),
        ("world.txt", 0o777), ("readonly.key", 0o400), ("notes.txt", 0o644),
        (cred_name, 0o600),
    ]
    rng.shuffle(specs)
    credpath = None
    for name, mode in specs:
        p = root + "/" + name
        if mode == 0o600:
            credpath = p
            body = "# credential material\ntoken = %s\nscope = %s\n" % (frag1, frag2)
        else:
            body = "# ordinary file\nsection = %s\n" % tok(rng, "n", 6)
        wfile(fs, p, body, mode=mode)
    checks = [
        hash_check("perm-audit-1", frag1, "Find the mode-0600 credential file and submit its token value."),
        hash_check("perm-audit-2", os.path.basename(credpath), "Submit the name of that credential file."),
    ]
    instr = (
        "One file under %s is a credential file left with mode 0600. Use ls -l, stat and "
        "find -perm to locate it, then submit the token it stores and its filename." % root
    )
    hints = [
        "Tier 1: ls -l <dir> prints permission bits like -rw------- for mode 0600.",
        "Tier 2: stat -c '%a %n' <files> prints the octal mode beside each filename.",
        "Tier 3: find <dir> -perm 0600 -type f selects only files with exactly that mode.",
    ]
    return instr, hints, checks


@template("noise-filter")
def t_noise_filter(rng, fs):
    logp = "/var/log/sre/noise-" + tok(rng, "n", 8) + ".log"
    n = 20000
    real = rng.randint(28, 55)
    frag = tok(rng, "fail")
    positions = set(rng.sample(range(1, n + 1), real))
    lines = []
    for i in range(1, n + 1):
        if i in positions:
            lines.append("%s checker[7]: FAIL job=%s backend timeout" % (ts(rng), frag))
        elif rng.random() < 0.82:
            lines.append("%s health[5]: heartbeat ok seq=%d" % (ts(rng), i))
        else:
            lines.append(sys_line(rng))
    wfile(fs, logp, "\n".join(lines) + "\n")
    checks = [
        hash_check("noise-filter-1", frag, "Filter out heartbeats and submit the failure job token."),
        hash_check("noise-filter-2", str(real), "Count the real FAIL lines and submit the number.", plain=str(real)),
    ]
    instr = (
        "The log %s is full of heartbeat noise. Use grep -v to remove the heartbeat lines, "
        "then find the real failure token and count how many failures occurred." % logp
    )
    hints = [
        "Tier 1: grep -v 'heartbeat' <log> drops lines containing the noise pattern.",
        "Tier 2: pipe filters together: grep -v heartbeat <log> | grep FAIL shows only real failures.",
        "Tier 3: grep -c 'FAIL' <log> counts the surviving failure lines.",
    ]
    return instr, hints, checks


@template("nano-replace")
def t_nano_replace(rng, fs):
    root = "/home/trainee/nano-" + tok(rng, "n", 8)
    cfg = root + "/hosts.conf"
    old = "oldhost-" + tok(rng, "o", 6)
    frag = "newhost-" + tok(rng, "h", 6)
    cnt = rng.randint(5, 9)
    lines = ["# service host map"]
    for i in range(cnt):
        lines.append("backend.%d.host = %s" % (i + 1, old))
    lines += ["timeout = 30", "retries = 4"]
    broken = "\n".join(lines) + "\n"
    fixed = "\n".join(x.replace(old, frag) for x in lines) + "\n"
    wfile(fs, cfg, broken)              # checked path: ships BROKEN (old hostname)
    checks = [
        file_check("nano-replace-1", cfg, fixed, frag,
                   "Replace every wrong hostname with the correct one.", initial=broken),
        hash_check("nano-replace-2", str(cnt), "Report how many wrong hostnames the file contained.", plain=str(cnt)),
    ]
    instr = (
        "Every backend in %s currently points at the wrong host %s. The corrected end state "
        "is that every occurrence of %s is replaced by %s and nothing else changes. Use "
        "nano's Ctrl+\\ (replace all) to do it in place, then report how many substitutions "
        "were needed." % (cfg, old, old, frag)
    )
    hints = [
        "Tier 1: grep -c '%s' <file> counts the wrong entries before you edit." % old,
        "Tier 2: in nano, Ctrl+\\ asks for a search string and a replacement, then y or a to confirm each or all.",
        "Tier 3: Ctrl+O saves the edited buffer and Ctrl+X exits nano.",
    ]
    return instr, hints, checks


@template("cut-paste")
def t_cut_paste(rng, fs):
    root = "/home/trainee/cutpaste-" + tok(rng, "k", 8)
    cfg = root + "/order.txt"
    frag1, frag2 = tok(rng, "top"), tok(rng, "mid")
    correct = [
        "STEP 1 bootstrap cluster",
        "STEP 2 pull images",
        "STEP 3 token=%s" % frag1,
        "STEP 4 configure routes",
        "STEP 5 token=%s" % frag2,
        "STEP 6 start workers",
        "STEP 7 verify health",
        "STEP 8 announce release",
    ]
    broken = list(correct)
    rng.shuffle(broken)
    while broken == correct:            # never ship an already-solved file
        rng.shuffle(broken)
    fixed = "\n".join(correct) + "\n"
    broken_content = "\n".join(broken) + "\n"
    wfile(fs, cfg, broken_content)      # checked path: ships BROKEN (scrambled)
    checks = [
        file_check("cut-paste-1", cfg, fixed, frag1,
                   "Restore the runbook lines into their correct order.", initial=broken_content),
        hash_check("cut-paste-2", frag2, "Submit the second token that belongs in step 5."),
    ]
    instr = (
        "The runbook at %s has its eight STEP lines scrambled. The corrected end state is "
        "the same eight lines ordered by their STEP numbers from 1 through 8, each keeping "
        "its original text (including the token lines that belong to STEP 3 and STEP 5), "
        "with no other changes. Use nano's Ctrl+k to cut a line and Ctrl+u to paste it back "
        "into place, then submit both embedded tokens." % cfg
    )
    hints = [
        "Tier 1: grep -n '^STEP' <file> shows the current order with line numbers.",
        "Tier 2: in nano, Ctrl+k cuts the current line into a buffer and Ctrl+u pastes it above the cursor.",
        "Tier 3: sort -n <file> previews what ascending STEP order would look like.",
    ]
    return instr, hints, checks


@template("nav-maze")
def t_nav_maze(rng, fs):
    root = "/home/trainee/maze-" + tok(rng, "z", 8)
    names = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
             "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron"]
    depth = rng.randint(7, 10)
    segs = rng.sample(names, depth)
    leaf = root + "/" + "/".join(segs)
    frag1, frag2 = tok(rng, "deep"), tok(rng, "mark")
    body = []
    for i in range(40):
        if i == 11:
            body.append("checkpoint = %s" % frag1)
        elif i == 29:
            body.append("landmark = %s" % frag2)
        else:
            body.append(cfg_line(rng, i))
    wfile(fs, leaf + "/notes.txt", "\n".join(body) + "\n")
    # a few decoy branches
    for k in range(8):
        d = root + "/" + "/".join(rng.sample(names, rng.randint(2, 5)))
        wfile(fs, d + "/readme.txt", "# decoy branch\n" + cfg_line(rng, k) + "\n")
    checks = [
        hash_check("nav-maze-1", frag1, "Navigate the maze and submit the checkpoint token."),
        hash_check("nav-maze-2", frag2, "Submit the landmark token stored in the same file."),
    ]
    instr = (
        "A deeply nested directory maze starts at %s. Use tree -L 2, cd -, pwd and ls -lah "
        "to navigate to the leaf directory, read notes.txt, and submit the checkpoint and "
        "landmark tokens." % root
    )
    hints = [
        "Tier 1: tree -L 2 <dir> shows two levels of a directory tree.",
        "Tier 2: cd - jumps back to the previous directory; pwd prints the current absolute path.",
        "Tier 3: ls -lah <dir> lists all entries with sizes and permissions.",
    ]
    return instr, hints, checks


# ---------------------------------------------------------------------------
# troubleshoot pilot templates (only used by --profile troubleshoot)
# ---------------------------------------------------------------------------

# Flat fixture directory consumed by the mock command layer in the image.
# The order here is the T16 contract order; every name is always emitted.
FIXTURE_NAMES = [
    "systemctl_status.txt", "journalctl_unit.txt", "journalctl_xe.txt",
    "ps_aux.txt", "ss_tlnp.txt", "curl_v.txt", "curl_I.txt", "df_h.txt",
    "free_m.txt", "top.txt", "du_sh.txt", "nginx_t.txt", "getenforce.txt",
    "ausearch_avc.txt", "dig.txt", "nslookup.txt", "ping.txt", "nc_zv.txt",
    "docker_ps_a.txt", "docker_logs.txt", "docker_inspect.txt",
    "podman_ps_a.txt", "podman_logs.txt", "podman_inspect.txt",
    "kubectl_get_pods.txt", "kubectl_get_svc.txt", "kubectl_get_endpoints.txt",
    "kubectl_describe_pod.txt", "kubectl_logs.txt", "kubectl_get_events.txt",
    "kubectl_get_configmap.txt", "kubectl_get_deploy.txt",
]
NEUTRAL_FIXTURE = "no data for this scenario\n"
# The Day 2 troubleshoot pool.  Order matters only for the seeded (no --pick)
# selection; --pick uses the caller's order verbatim.  Each id is one stage
# template; every run still emits all 32 fixtures above (unused = NEUTRAL).
TROUBLESHOOT_IDS = [
    "svc-crashloop", "k8s-crashloop", "disk-full", "container-crash",
    "nginx-config", "dns-mismatch", "selinux-avc", "port-conflict",
    "oom-kill", "image-pull", "svc-selector", "resource-limits",
    "tls-expiry", "dependency-down",
]
TROUBLESHOOT_TEMPLATES = {}


def troubleshoot_template(tid):
    def deco(fn):
        TROUBLESHOOT_TEMPLATES[tid] = fn
        return fn
    return deco


def hexs(rng, n=4):
    """Short deterministic lowercase hex suffix for randomized names."""
    return "".join(rng.choice(HEX) for _ in range(n))


def put_fixture(fixtures, name, content, tid):
    if name not in FIXTURE_NAMES:
        raise ValueError("%s: unknown fixture %r" % (tid, name))
    if name in fixtures and fixtures[name] != content:
        raise ValueError("%s: conflicting content for fixture %s" % (tid, name))
    fixtures[name] = content


def merge_ss_fixtures(left, right):
    """Compose listening-socket rows without dropping either stage's evidence.

    The flat fixture namespace is shared by every selected stage.  svc-crashloop
    and port-conflict both need real ``ss -tlnp`` rows, so keep one header and a
    canonical, de-duplicated set of rows.  Sorting makes the result independent
    of PICK order as well as deterministic for a seed.
    """
    headers = []
    rows = set()
    for text in (left, right):
        for line in text.splitlines():
            if line.startswith("State "):
                if line not in headers:
                    headers.append(line)
            elif line.strip():
                rows.add(line)
    return "\n".join(headers + sorted(rows)) + "\n"


def merge_curl_fixtures(left, right):
    """Compose labelled curl transcripts while preserving every diagnostic."""
    blocks = set()
    for text in (left, right):
        for block in text.strip().split("\n\n"):
            if block.strip():
                blocks.add(block.strip("\n"))
    return "\n\n".join(sorted(blocks)) + "\n"


def merge_reports(left, right):
    """Compose whole labelled command reports in canonical order.

    Keeping each report intact avoids inventing a combined process table or
    interleaving unrelated timestamps, while sorting the reports makes the
    bytes independent of PICK order.
    """
    reports = {left.rstrip("\n"), right.rstrip("\n")}
    return "\n\n".join(sorted(reports)) + "\n"


def merge_k8s_reports(left, right):
    """Merge any number of labelled kubectl describe/log reports associatively."""
    reports = set()
    for text in (left, right):
        current = []
        for line in text.rstrip("\n").splitlines():
            if line.startswith("# kubectl ") and current:
                reports.add("\n".join(current).strip("\n"))
                current = [line]
            else:
                current.append(line)
        if current:
            reports.add("\n".join(current).strip("\n"))
    return "\n\n".join(sorted(reports)) + "\n"


def merge_labelled_reports(left, right):
    """Merge labelled command reports associatively without losing any evidence.

    Status, journal, DNS, ping and nc fixtures can be requested by several
    selected drills.  Each report starts with a ``# <command ...>`` line; parse
    those boundaries rather than treating an already-merged fixture as one
    opaque report.  Sorting makes the result independent of PICK order.
    """
    reports = set()
    for text in (left, right):
        current = []
        for line in text.rstrip("\n").splitlines():
            if line.startswith("# ") and current:
                reports.add("\n".join(current).strip("\n"))
                current = [line]
            else:
                current.append(line)
        if current:
            reports.add("\n".join(current).strip("\n"))
    return "\n\n".join(sorted(reports)) + "\n"


def merge_table_rows(left, right):
    """Keep one canonical table header and every Kubernetes evidence row."""
    headers = set()
    rows = set()
    for text in (left, right):
        for line in text.splitlines():
            if line.startswith("NAME ") or line.startswith("LAST SEEN "):
                headers.add(line)
            elif line.strip():
                rows.add(line)
    if not headers:
        raise ValueError("Kubernetes table merge has no header")
    return "\n".join([sorted(headers)[0]] + sorted(rows)) + "\n"


def merge_container_ps_fixtures(left, right):
    """Keep one container-table header and every stage's process row."""
    headers = []
    rows = set()
    for text in (left, right):
        lines = [line for line in text.splitlines() if line.strip()]
        if lines and lines[0].startswith("CONTAINER ID"):
            if lines[0] not in headers:
                headers.append(lines[0])
            lines = lines[1:]
        rows.update(lines)
    return "\n".join(headers + sorted(rows)) + "\n"


def merge_inspect_fixtures(left, right):
    """Merge Docker/Podman inspection arrays without dropping either object."""
    objects = {}
    for text in (left, right):
        for obj in json.loads(text):
            key = json.dumps(obj, sort_keys=True, separators=(",", ":"))
            objects[key] = obj
    ordered = [objects[key] for key in sorted(objects)]
    return json.dumps(ordered, separators=(",", ":")) + "\n"


def put_fixture_merged(fixtures, name, content, tid, merge):
    """Hard-claim a fixture, composing it when another stage already claimed it.

    Ordinary fixtures retain put_fixture's conflict-is-a-bug behaviour.  Only
    table/transcript fixtures with an explicit, tested merge use this helper.
    """
    if name not in FIXTURE_NAMES:
        raise ValueError("%s: unknown fixture %r" % (tid, name))
    if name not in fixtures:
        fixtures[name] = content
        return
    previous = fixtures[name]
    if previous == content:
        return
    merged = merge(previous, content)
    if merged == previous or merged == content:
        raise ValueError("%s: fixture merge for %s discarded evidence" % (tid, name))
    fixtures[name] = merged


def put_soft(soft, fixtures, name, content, tid):
    """Claim a fixture that may legitimately be owned by another stage.

    The flat fixture namespace means only one stage can define a given file.
    ``disk-full`` wants to show a journal, but ``svc-crashloop`` already owns
    ``journalctl_unit.txt``; when both are selected the hard fixture must win,
    in either build order.  A soft entry is used only if no hard fixture claims
    the same name by the time the run is written out.
    """
    if name not in FIXTURE_NAMES:
        raise ValueError("%s: unknown fixture %r" % (tid, name))
    if name in fixtures:
        return                       # a hard fixture already owns it; drop this
    if name in soft and soft[name] != content:
        raise ValueError("%s: conflicting soft content for fixture %s" % (tid, name))
    soft[name] = content


@troubleshoot_template("svc-crashloop")
def t_svc_crashloop(rng, fs, fixtures, soft):
    """Broken systemd unit: crash loop, port conflict and a drifted DB host."""
    svc = tok(rng, "svc", 6)
    unit = svc + ".service"
    cfg = "/etc/%s/app.conf" % svc
    unit_path = "/etc/systemd/system/" + unit
    agent = svc + "-agent"

    held_port = rng.randint(40000, 49000)
    free_port = rng.randint(51000, 59999)
    holder = tok(rng, "holder", 6)
    holder_pid = rng.randint(1500, 32000)
    holder_fd = rng.randint(8, 40)
    err = tok(rng, "E", 8)
    old_db = "db-old-%s.internal.example" % hexs(rng, 4)
    new_db = "db-new-%s.internal.example" % hexs(rng, 4)

    base = [
        "# service configuration managed by the troubleshoot pilot",
        "service_name = %s" % svc,
        "bind_port = %d" % held_port,
        "db_host = %s" % old_db,
        "log_level = warn",
    ]
    fixed_lines = list(base)
    fixed_lines[2] = "bind_port = %d" % free_port
    fixed_lines[3] = "db_host = %s" % new_db
    broken = "\n".join(base) + "\n"
    fixed = "\n".join(fixed_lines) + "\n"
    # The checked config is deliberately writable by the unprivileged trainee;
    # the unit file is only context and is never a checked path.
    wfile(fs, cfg, broken, mode=0o666)
    wfile(fs, unit_path, "\n".join([
        "[Unit]",
        "Description=%s demo service (troubleshoot pilot)" % svc,
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        "ExecStart=/usr/local/bin/%s --config %s" % (agent, cfg),
        "Restart=always",
        "RestartSec=2",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
    ]) + "\n")

    # systemctl status carries the same error code as the journal.
    put_fixture_merged(fixtures, "systemctl_status.txt", "\n".join([
        "# systemctl status %s" % unit,
        "* %s - %s demo service (troubleshoot pilot)" % (unit, svc),
        "     Loaded: loaded (%s; enabled; vendor preset: enabled)" % unit_path,
        "     Active: active (auto-restart) for 3min ago",
        "   Main PID: %d (code=exited, status=1/FAILURE)" % (holder_pid + 1),
        "        CPU: 42ms",
        "",
        "%s systemd[1]: %s: Main process exited, code=exited, status=1/FAILURE" % (ts(rng), unit),
        "%s %s[%d]: FATAL: startup aborted with error %s" % (ts(rng), agent, holder_pid + 1, err),
        "%s systemd[1]: %s: Failed with result 'exit-code'." % (ts(rng), unit),
    ]) + "\n", "svc-crashloop", merge_labelled_reports)

    # Several thousand lines of journal, with repeated crash messages and two
    # clues: the reserved replacement port and the post-failover DB endpoint.
    n_lines = rng.randint(3600, 4400)
    crash_every = rng.randint(37, 73)
    db_clue = rng.randint(n_lines // 3, (2 * n_lines) // 3)
    port_clue = rng.randint(n_lines // 4, n_lines // 3)
    while db_clue % crash_every == 0:
        db_clue += 1
    while port_clue % crash_every == 0 or port_clue == db_clue:
        port_clue += 1
    journal = []
    for i in range(1, n_lines + 1):
        if i % crash_every == 0:
            journal.append(
                "%s %s[%d]: ERROR startup failed for %s (error %s, exit status 1); restart %d"
                % (ts(rng), agent, holder_pid + 1, unit, err, i // crash_every)
            )
        elif i == db_clue:
            journal.append(
                "%s %s[%d]: INFO database failover complete; new primary endpoint is %s"
                % (ts(rng), agent, holder_pid + 1, new_db)
            )
        elif i == port_clue:
            journal.append(
                "%s %s[%d]: WARN configured port %d is bound by %s; reserved replacement port is %d"
                % (ts(rng), agent, holder_pid + 1, held_port, holder, free_port)
            )
        else:
            journal.append(sys_line(rng))
    put_fixture_merged(fixtures, "journalctl_unit.txt",
                       "# journalctl -u %s -n 100 --no-pager\n-- unit %s --\n%s"
                       % (unit, unit, "\n".join(journal) + "\n"),
                       "svc-crashloop", merge_labelled_reports)
    put_fixture_merged(fixtures, "journalctl_xe.txt", "\n".join([
        "# journalctl -xe",
        "-- Boot 6f2c1d4e9a844b3c8b1e7d0a5f3c9d21 --",
        "-- unit %s --" % unit,
        "%s systemd[1]: %s: Main process exited, code=exited, status=1/FAILURE" % (ts(rng), unit),
        "%s %s[%d]: FATAL: error %s while binding port %d (address already in use)"
        % (ts(rng), agent, holder_pid + 1, err, held_port),
        "%s systemd[1]: %s: Failed with result 'exit-code'." % (ts(rng), unit),
        "Hint: journalctl -u %s -n 100 --no-pager shows the full unit history." % unit,
    ]) + "\n", "svc-crashloop", merge_labelled_reports)
    put_fixture_merged(fixtures, "ss_tlnp.txt", "\n".join([
        "State   Recv-Q  Send-Q  Local Address:Port   Peer Address:Port  Process",
        'LISTEN  0       128     0.0.0.0:%-11d 0.0.0.0:*          users:(("%s",pid=%d,fd=%d))'
        % (held_port, holder, holder_pid, holder_fd),
        'LISTEN  0       128     127.0.0.1:22       0.0.0.0:*          users:(("sshd",pid=411,fd=3))',
        'LISTEN  0       128     0.0.0.0:5355      0.0.0.0:*          users:(("resolved",pid=208,fd=12))',
    ]) + "\n", "svc-crashloop", merge_ss_fixtures)

    # Load-bearing invariants: the clues must be discoverable and must not be
    # pre-filled in the broken config, and the conflict must be visible in ss.
    if new_db in broken or old_db not in broken:
        raise AssertionError("svc-crashloop: DB host clue/broken state inconsistent")
    if sum("reserved replacement port is" in line for line in journal) != 1:
        raise AssertionError("svc-crashloop: replacement-port clue is not unique")
    if ("%d" % held_port) not in fixtures["ss_tlnp.txt"]:
        raise AssertionError("svc-crashloop: conflicting port missing from ss output")
    if ("%d" % free_port) in fixtures["ss_tlnp.txt"]:
        raise AssertionError("svc-crashloop: replacement port is not actually free")
    if err not in fixtures["systemctl_status.txt"] or err not in "\n".join(journal):
        raise AssertionError("svc-crashloop: error code missing from status/journal")
    if holder not in fixtures["ss_tlnp.txt"]:
        raise AssertionError("svc-crashloop: port holder missing from ss output")

    svc_answer = "%s:%s" % (svc, err)
    checks = [
        hash_check("svc-crashloop-1", svc_answer,
                   "Submit the failing service name and its error code, joined by a colon "
                   "(service first), as found in systemctl status and journalctl -u."),
        file_check("svc-crashloop-2", cfg, fixed, "port-%d" % free_port,
                   "Repair %s in place: change only bind_port to the reserved free port and "
                   "db_host to the post-failover endpoint named in the journal." % cfg,
                   initial=broken),
        hash_check("svc-crashloop-3", holder,
                   "Submit the process name shown holding the configured port in ss -tlnp."),
    ]
    instr = (
        "The service %s is crash-looping. Its configuration is %s and its unit is %s. "
        "Use `systemctl status %s`, `journalctl -u %s -n 100 --no-pager` and "
        "`journalctl -xe` to find the service name, the repeated error code, the reserved "
        "replacement port and the post-failover endpoint. Use `ss -tlnp` to "
        "identify the process that already holds the configured port.\n\n"
        "The checked config is %s. Its corrected end state is byte-for-byte the shipped "
        "file with exactly two changes: `bind_port` becomes the reserved free port named "
        "in the journal, and `db_host` becomes the post-failover endpoint named in the journal. "
        "Leave every other line unchanged and edit the file in place."
        % (unit, cfg, unit_path, unit, unit, cfg)
    )
    hints = [
        "Checklist: systemctl status <unit> first; then journalctl -u <unit> -n 100 "
        "--no-pager and journalctl -xe for the repeated crash signature.",
        "Checklist: ss -tlnp lists listening ports and their owning processes; a port "
        "that is not in LISTEN is free. The journal records which port was reserved.",
        "Checklist: read the journal for the post-failover endpoint, then edit "
        "only the bind_port and db_host lines of the config in place with nano. Report "
        "the process name exactly as printed by ss -tlnp.",
    ]
    return instr, hints, checks


@troubleshoot_template("k8s-crashloop")
def t_k8s_crashloop(rng, fs, fixtures, soft):
    """Mocked kubectl: a CrashLoopBackOff caused by the wrong configmap key."""
    ns = tok(rng, "ns", 6)
    dep = tok(rng, "dep", 6)
    pod = "%s-%s-%s" % (dep, hexs(rng, 6), hexs(rng, 5))
    cm = tok(rng, "cm", 6)
    reason = tok(rng, "K8S", 6)
    missing_key = "DB_HOST_" + hexs(rng, 4)
    actual_key = "DB_HOSTNAME_" + hexs(rng, 4)
    db_value = "db-%s.internal.example" % hexs(rng, 6)
    restarts = rng.randint(9, 30)
    manifest = "/home/trainee/k8s/%s.yaml" % dep

    yaml_lines = [
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata:",
        "  name: %s" % dep,
        "  namespace: %s" % ns,
        "spec:",
        "  replicas: 1",
        "  selector:",
        "    matchLabels:",
        "      app: %s" % dep,
        "  template:",
        "    metadata:",
        "      labels:",
        "        app: %s" % dep,
        "    spec:",
        "      containers:",
        "        - name: api",
        "          image: registry.example/svc-api:1.4.2",
        "          env:",
        "            - name: DB_HOST",
        "              valueFrom:",
        "                configMapKeyRef:",
        "                  name: %s" % cm,
        "                  key: %s" % missing_key,
    ]
    broken = "\n".join(yaml_lines) + "\n"
    fixed_lines = list(yaml_lines)
    fixed_lines[-1] = "                  key: %s" % actual_key
    fixed = "\n".join(fixed_lines) + "\n"
    wfile(fs, manifest, broken)

    put_fixture_merged(fixtures, "kubectl_get_pods.txt", "\n".join([
        "NAME                                     READY   STATUS             RESTARTS   AGE",
        "%-40s 0/1     CrashLoopBackOff   %-9d 42m" % (pod, restarts),
    ]) + "\n", "k8s-crashloop", merge_table_rows)
    put_fixture_merged(fixtures, "kubectl_describe_pod.txt",
                       "# kubectl describe pod %s\n%s" % (pod, "\n".join([
        "Name:             %s" % pod,
        "Namespace:        %s" % ns,
        "Labels:           app=%s" % dep,
        "Status:           Running",
        "Containers:",
        "  api:",
        "    Image:        registry.example/svc-api:1.4.2",
        "    State:        Waiting  (CrashLoopBackOff)",
        "    Last State:   Terminated  (Error, exit code 1)",
        "    Environment:",
        "      DB_HOST:  <set from configmap %s, key %s>" % (cm, missing_key),
        "      (configMapKeyRef name=%s key=%s)" % (cm, missing_key),
        "Events:  <use `kubectl get events` for details>",
    ]) + "\n"), "k8s-crashloop", merge_k8s_reports)
    log_lines = []
    for attempt in range(1, 7):
        log_lines.append("%s api: starting svc-api 1.4.2 (attempt %d)" % (ts(rng), attempt))
        log_lines.append("%s api: FATAL config lookup failed: key '%s' is missing in configmap '%s'; reason %s"
                         % (ts(rng), missing_key, cm, reason))
        log_lines.append("%s api: exiting with status 1" % ts(rng))
    put_fixture_merged(fixtures, "kubectl_logs.txt",
                       "# kubectl logs %s\n%s" % (pod, "\n".join(log_lines) + "\n"),
                       "k8s-crashloop", merge_k8s_reports)
    put_fixture_merged(fixtures, "kubectl_get_events.txt", "\n".join([
        "LAST SEEN   TYPE      REASON    OBJECT          MESSAGE",
        "2m          Warning   BackOff   pod/%s  Back-off restarting failed container (code %s)"
        % (pod, reason),
        "3m          Warning   ConfigKey pod/%s  configmap '%s' defines key '%s' but the pod references a different key"
        % (pod, cm, actual_key),
        "5m          Normal    Pulled    pod/%s  Container image already present" % pod,
        "6m          Warning   Unhealthy pod/%s  Readiness probe failed" % pod,
    ]) + "\n", "k8s-crashloop", merge_table_rows)
    put_fixture(fixtures, "kubectl_get_configmap.txt", "\n".join([
        "Name:         %s" % cm,
        "Namespace:    %s" % ns,
        "Data",
        "====",
        "%s: %s" % (actual_key, db_value),
    ]) + "\n", "k8s-crashloop")
    put_fixture_merged(fixtures, "kubectl_get_deploy.txt", "\n".join([
        "NAME                 READY   UP-TO-DATE   AVAILABLE   AGE",
        "%-20s 0/1     1            0           42m" % dep,
    ]) + "\n", "k8s-crashloop", merge_table_rows)

    if missing_key == actual_key or missing_key not in fixtures["kubectl_logs.txt"]:
        raise AssertionError("k8s-crashloop: missing key clue inconsistent")
    if missing_key in fixtures["kubectl_get_configmap.txt"]:
        raise AssertionError("k8s-crashloop: missing key must not exist in the configmap")
    if actual_key not in fixtures["kubectl_get_configmap.txt"] or actual_key in broken:
        raise AssertionError("k8s-crashloop: actual key clue/broken state inconsistent")
    if reason not in fixtures["kubectl_logs.txt"]:
        raise AssertionError("k8s-crashloop: crash reason code missing from logs")
    if "CrashLoopBackOff" not in fixtures["kubectl_get_pods.txt"]:
        raise AssertionError("k8s-crashloop: pod status is not CrashLoopBackOff")

    pod_answer = "%s:%s" % (pod, reason)
    checks = [
        hash_check("k8s-crashloop-1", pod_answer,
                   "Submit the crashing pod name and its crash reason code, joined by a "
                   "colon (pod first), as found in kubectl get pods and kubectl logs."),
        hash_check("k8s-crashloop-2", missing_key,
                   "Submit the config key the container says is missing in its logs."),
        file_check("k8s-crashloop-3", manifest, fixed, actual_key,
                   "Repair %s in place: change only the configMapKeyRef key to the key "
                   "actually present in the configmap." % manifest,
                   initial=broken),
    ]
    instr = (
        "A pod in namespace %s is in CrashLoopBackOff. Use `kubectl get pods`, "
        "`kubectl describe pod <pod>`, `kubectl logs <pod>`, `kubectl get events` and "
        "`kubectl get configmap <name>` to find the crashing pod, its crash reason code, "
        "the config key the container asks for, and the key the configmap actually holds.\n\n"
        "The deployment manifest to repair is %s. Its corrected end state is "
        "byte-for-byte the shipped file with exactly one change: the "
        "configMapKeyRef `key` value becomes the key actually present in the configmap. "
        "Leave every other line (including the env var name and configmap name) unchanged "
        "and edit the file in place."
        % (ns, manifest)
    )
    hints = [
        "Checklist: kubectl get pods shows the status; kubectl describe pod <pod> shows "
        "which configmap key the container environment requests.",
        "Checklist: kubectl logs <pod> and kubectl get events show the crash reason; "
        "kubectl get configmap <name> lists the real keys.",
        "Checklist: edit the configMapKeyRef key line in the manifest in place with nano; "
        "the env var name and configmap name are not the problem.",
    ]
    return instr, hints, checks


@troubleshoot_template("svc-selector")
def t_svc_selector(rng, fs, fixtures, soft):
    """Kubernetes Service triage: pod labels do not match the Service selector."""
    ns = tok(rng, "ns", 6)
    svc = tok(rng, "frontend", 6)
    dep = tok(rng, "webdeploy", 6)
    pod = "%s-%s-%s" % (dep, hexs(rng, 6), hexs(rng, 5))
    release = tok(rng, "release", 6)
    mismatch = tok(rng, "selector-mismatch", 8)
    service_port = rng.choice([80, 8080, 8443])
    cluster_ip = ip(rng)
    connection_fragment = "conn-%s" % hexs(rng, 8)
    manifest = "/home/trainee/k8s/%s.yaml" % dep
    target_labels = "tier=web,release=%s" % release

    yaml_lines = [
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata:",
        "  name: %s" % dep,
        "  namespace: %s" % ns,
        "spec:",
        "  replicas: 2",
        "  selector:",
        "    matchLabels:",
        "      tier: web",
        "  template:",
        "    metadata:",
        "      labels:",
        "        tier: web",
        "        release: %s" % release,
        "    spec:",
        "      containers:",
        "        - name: web",
        "          image: registry.example/frontend:2.1.0",
        "          ports:",
        "            - containerPort: %d" % service_port,
    ]
    broken = "\n".join(yaml_lines) + "\n"
    fixed_lines = list(yaml_lines)
    labels_index = fixed_lines.index("      labels:")
    fixed_lines.insert(labels_index + 1, "        app: %s" % svc)
    fixed = "\n".join(fixed_lines) + "\n"
    wfile(fs, manifest, broken, mode=0o644)

    pod_rows = [
        "NAME                          READY   STATUS             RESTARTS   AGE   LABELS",
        "%-30s 0/1     Pending           %-9d 7m     %s"
        % (pod, 0, target_labels),
    ]
    for _ in range(4):
        decoy = "%s-%s-%s" % (tok(rng, "pod", 4), hexs(rng, 4), hexs(rng, 3))
        decoy_labels = "app=%s,tier=web" % hexs(rng, 6)
        pod_rows.append("%-30s 1/1     Running            %-9d 12m    %s"
                        % (decoy, 0, decoy_labels))
    put_fixture_merged(fixtures, "kubectl_get_pods.txt", "\n".join(pod_rows) + "\n",
                       "svc-selector", merge_table_rows)

    put_fixture(fixtures, "kubectl_get_svc.txt", "\n".join([
        "NAME                         TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)   AGE   SELECTOR",
        "%-28s ClusterIP   %-13s <none>         %d/TCP    7m    app=%s"
        % (svc, cluster_ip, service_port, svc),
        "endpoint-controller: <none> (%s)" % mismatch,
    ]) + "\n", "svc-selector")
    put_fixture(fixtures, "kubectl_get_endpoints.txt", "\n".join([
        "NAME                         ENDPOINTS   AGE",
        "%-28s <none>      7m" % svc,
        "controller: %s matched=0 selector=app=%s" % (mismatch, svc),
    ]) + "\n", "svc-selector")
    put_fixture_merged(fixtures, "kubectl_describe_pod.txt",
                       "# kubectl describe pod %s\n%s" % (pod, "\n".join([
        "Name:             %s" % pod,
        "Namespace:        %s" % ns,
        "Labels:           %s" % target_labels,
        "Status:           Pending",
        "Containers:",
        "  web:",
        "    Image:        registry.example/frontend:2.1.0",
        "    State:        Waiting (PodInitializing)",
        "Service:         <none> (selector app=%s does not match pod labels)" % svc,
        "Endpoint controller: %s" % mismatch,
        "Events:          <use `kubectl get events` for details>",
    ]) + "\n"), "svc-selector", merge_k8s_reports)
    put_fixture_merged(fixtures, "kubectl_get_events.txt", "\n".join([
        "LAST SEEN   TYPE      REASON    OBJECT          MESSAGE",
        "4m          Warning   EndpointsNotFound service/%s  no endpoints: %s; selector app=%s matched 0 pods"
        % (svc, mismatch, svc),
    ]) + "\n", "svc-selector", merge_table_rows)
    put_fixture_merged(fixtures, "kubectl_get_deploy.txt", "\n".join([
        "NAME                 READY   UP-TO-DATE   AVAILABLE   AGE",
        "%-20s 0/2     2            0           7m" % dep,
    ]) + "\n", "svc-selector", merge_table_rows)
    put_fixture_merged(fixtures, "curl_v.txt", "\n".join([
        "# curl -v http://%s:%d/" % (cluster_ip, service_port),
        "*   Trying %s:%d..." % (cluster_ip, service_port),
        "* connect to %s port %d failed: Connection refused"
        % (cluster_ip, service_port),
        "* connection %s: refused" % connection_fragment,
        "* Closing connection 0",
    ]) + "\n\n", "svc-selector", merge_curl_fixtures)

    if "app=%s" % svc in target_labels or "        app: %s" % svc in broken:
        raise AssertionError("svc-selector: broken pod labels already match the Service")
    if fixed != broken.replace("      labels:\n",
                               "      labels:\n        app: %s\n" % svc):
        raise AssertionError("svc-selector: repair is not exactly the missing pod label")
    if fixed == broken or len(fixed_lines) != len(yaml_lines) + 1:
        raise AssertionError("svc-selector: repaired deployment does not add one line")
    if svc not in fixtures["kubectl_get_svc.txt"] or "app=%s" % svc not in fixtures["kubectl_get_svc.txt"]:
        raise AssertionError("svc-selector: Service name/selector missing from get svc")
    if mismatch not in fixtures["kubectl_get_endpoints.txt"] \
            or mismatch not in fixtures["kubectl_get_events.txt"]:
        raise AssertionError("svc-selector: mismatch fragment missing from endpoint evidence")
    if "<none>" not in fixtures["kubectl_get_endpoints.txt"] \
            or "EndpointsNotFound" not in fixtures["kubectl_get_events.txt"]:
        raise AssertionError("svc-selector: missing-endpoint state not shown")
    if pod not in fixtures["kubectl_get_pods.txt"] or pod not in fixtures["kubectl_describe_pod.txt"]:
        raise AssertionError("svc-selector: affected pod missing from shared fixtures")
    if cluster_ip not in fixtures["curl_v.txt"] or "Connection refused" not in fixtures["curl_v.txt"]:
        raise AssertionError("svc-selector: curl evidence missing for the Service address")

    checks = [
        hash_check("svc-selector-1", svc,
                   "Submit the Service name from `kubectl get svc` whose selector is "
                   "not selecting any pods."),
        hash_check("svc-selector-2", mismatch,
                   "Submit the selector-mismatch fragment printed by `kubectl get endpoints` "
                   "or the EndpointsNotFound event."),
        file_check("svc-selector-3", manifest, fixed, "selector-" + dep,
                   "Repair %s in place: add only the `app` pod-template label named by "
                   "the Service selector, leaving the existing labels and Deployment "
                   "selector unchanged." % manifest,
                   initial=broken),
    ]
    instr = (
        "A Kubernetes Service has no ready endpoints and connections to its cluster address "
        "are refused. Use `kubectl get svc` to identify the Service and its selector, then "
        "correlate `kubectl get endpoints`, `kubectl get events`, `kubectl get pods`, "
        "`kubectl describe pod <pod>`, `kubectl get deploy <name>` and "
        "`curl -v http://<cluster-ip>:<port>/`.\n\n"
        "The deployment manifest to repair is %s. Its corrected end state is byte-for-byte "
        "the shipped file with exactly one new line under `spec.template.metadata.labels`: "
        "add the label key and value named by the Service selector. Keep both existing pod "
        "labels, the Deployment selector, image, port and every other byte unchanged, and "
        "edit the file in place." % manifest
    )
    hints = [
        "Checklist: kubectl get svc shows NAME and SELECTOR; use that selector, not the "
        "Deployment's tier selector, when comparing pod labels.",
        "Checklist: kubectl get endpoints and kubectl get events expose the no-endpoint "
        "state and a unique selector-mismatch token; describe pod corroborates the labels.",
        "Checklist: add only the missing Service-selector label beneath the pod template's "
        "labels in nano; Ctrl+O writes in place and Ctrl+X exits.",
    ]
    return instr, hints, checks


@troubleshoot_template("resource-limits")
def t_resource_limits(rng, fs, fixtures, soft):
    """Kubernetes resource triage: BestEffort pod was OOMKilled without a limit."""
    ns = tok(rng, "ns", 6)
    dep = tok(rng, "limits", 6)
    pod = "%s-%s-%s" % (dep, hexs(rng, 6), hexs(rng, 5))
    labels = "app=%s,tier=batch,build=%s" % (dep, hexs(rng, 6))
    oom_fragment = tok(rng, "oom", 8)
    memory_limit = rng.choice(["128Mi", "256Mi", "384Mi", "512Mi", "768Mi"])
    restarts = rng.randint(8, 24)
    manifest = "/home/trainee/k8s/%s.yaml" % dep

    yaml_lines = [
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata:",
        "  name: %s" % dep,
        "  namespace: %s" % ns,
        "spec:",
        "  replicas: 1",
        "  selector:",
        "    matchLabels:",
        "      app: %s" % dep,
        "  template:",
        "    metadata:",
        "      labels:",
        "        app: %s" % dep,
        "        tier: batch",
        "    spec:",
        "      containers:",
        "        - name: worker",
        "          image: registry.example/batch-worker:3.4.1",
        "          ports:",
        "            - containerPort: 9090",
    ]
    broken = "\n".join(yaml_lines) + "\n"
    fixed_lines = list(yaml_lines)
    image_index = fixed_lines.index("          image: registry.example/batch-worker:3.4.1")
    fixed_lines[image_index + 1:image_index + 1] = [
        "          resources:",
        "            limits:",
        "              memory: %s" % memory_limit,
    ]
    fixed = "\n".join(fixed_lines) + "\n"
    wfile(fs, manifest, broken, mode=0o644)

    pod_rows = [
        "NAME                          READY   STATUS             RESTARTS   AGE   LABELS",
        "%-30s 0/1     CrashLoopBackOff   %-9d 18m    %s"
        % (pod, restarts, labels),
    ]
    for _ in range(3):
        decoy = "%s-%s-%s" % (tok(rng, "batch", 4), hexs(rng, 4), hexs(rng, 3))
        decoy_labels = "app=%s,tier=batch" % hexs(rng, 6)
        pod_rows.append("%-30s 1/1     Running            %-9d 2h     %s"
                        % (decoy, 0, decoy_labels))
    put_fixture_merged(fixtures, "kubectl_get_pods.txt", "\n".join(pod_rows) + "\n",
                       "resource-limits", merge_table_rows)
    put_fixture_merged(fixtures, "kubectl_describe_pod.txt",
                       "# kubectl describe pod %s\n%s" % (pod, "\n".join([
        "Name:             %s" % pod,
        "Namespace:        %s" % ns,
        "Labels:           %s" % labels,
        "QoS Class:        BestEffort",
        "Containers:",
        "  worker:",
        "    Image:        registry.example/batch-worker:3.4.1",
        "    State:        Waiting (CrashLoopBackOff)",
        "    Last State:   Terminated (OOMKilled, exit code 137)",
        "    Limits:        <none>",
        "Incident token:  %s" % oom_fragment,
        "Memory policy:   set resources.limits.memory to %s" % memory_limit,
        "Events:          <use `kubectl get events` for details>",
    ]) + "\n"), "resource-limits", merge_k8s_reports)
    put_fixture_merged(fixtures, "kubectl_get_events.txt", "\n".join([
        "LAST SEEN   TYPE      REASON    OBJECT          MESSAGE",
        "3m          Warning   OOMKilling pod/%s  container worker was OOMKilled; incident %s"
        % (pod, oom_fragment),
        "4m          Warning   BackOff   pod/%s  Back-off restarting failed container" % pod,
        "6m          Normal    MemoryPolicy pod/%s  workload review: set resources.limits.memory to %s"
        % (pod, memory_limit),
    ]) + "\n", "resource-limits", merge_table_rows)
    put_fixture_merged(fixtures, "kubectl_logs.txt",
                       "# kubectl logs %s\n%s" % (pod, "\n".join([
        "%s worker: processing batch with growing working set" % ts(rng),
        "%s worker: terminated by OOM killer (incident %s)" % (ts(rng), oom_fragment),
        "%s supervisor: recommended resources.limits.memory=%s"
        % (ts(rng), memory_limit),
    ]) + "\n"), "resource-limits", merge_k8s_reports)
    put_fixture_merged(fixtures, "kubectl_get_deploy.txt", "\n".join([
        "NAME                 READY   UP-TO-DATE   AVAILABLE   AGE",
        "%-20s 0/1     1            0           18m" % dep,
    ]) + "\n", "resource-limits", merge_table_rows)

    if "resources:" in broken or ("memory: %s" % memory_limit) in broken:
        raise AssertionError("resource-limits: broken deployment already has the memory limit")
    resource_lines = [
        "          resources:",
        "            limits:",
        "              memory: %s" % memory_limit,
    ]
    if fixed_lines[image_index + 1:image_index + 4] != resource_lines \
            or len(fixed_lines) != len(yaml_lines) + 3 or fixed == broken:
        raise AssertionError("resource-limits: repair is not exactly the missing resource block")
    if pod not in fixtures["kubectl_get_pods.txt"] or "CrashLoopBackOff" not in fixtures["kubectl_get_pods.txt"]:
        raise AssertionError("resource-limits: crashing pod missing from shared pod table")
    describe = fixtures["kubectl_describe_pod.txt"]
    events = fixtures["kubectl_get_events.txt"]
    logs = fixtures["kubectl_logs.txt"]
    if "QoS Class:        BestEffort" not in describe or "Limits:        <none>" not in describe:
        raise AssertionError("resource-limits: BestEffort/no-limits evidence missing")
    if "OOMKilled" not in describe or oom_fragment not in describe or oom_fragment not in events:
        raise AssertionError("resource-limits: OOM state/fragment missing from describe/events")
    if "OOMKilling" not in events or memory_limit not in events or memory_limit not in describe:
        raise AssertionError("resource-limits: recommended memory limit is not revealed")
    if oom_fragment not in logs or memory_limit not in logs:
        raise AssertionError("resource-limits: logs do not corroborate OOM and memory limit")

    checks = [
        hash_check("resource-limits-1", pod,
                   "Submit the name of the CrashLoopBackOff pod whose last termination was "
                   "OOMKilled, as shown by `kubectl get pods` and `kubectl describe pod`."),
        hash_check("resource-limits-2", oom_fragment,
                   "Submit the OOM incident fragment printed by `kubectl get events` or "
                   "`kubectl describe pod`."),
        file_check("resource-limits-3", manifest, fixed, "limits-" + dep,
                   "Repair %s in place: insert only `resources.limits.memory` under the "
                   "worker container using the memory limit revealed by the event and "
                   "description evidence; leave every existing line unchanged." % manifest,
                   initial=broken),
    ]
    instr = (
        "A Kubernetes worker pod is crash-looping after an OOM kill. Use `kubectl get pods`, "
        "`kubectl describe pod <pod>`, `kubectl get events`, `kubectl logs <pod>` and "
        "`kubectl get deploy <name>` to identify the pod, confirm its QoS and missing limits, "
        "and recover the OOM incident token and reviewed memory-limit value.\n\n"
        "The deployment manifest to repair is %s. Its corrected end state is byte-for-byte "
        "the shipped file with exactly one new `resources` block inserted immediately after "
        "the worker image: `resources`, then `limits`, then `memory` set to the value revealed "
        "by the evidence. Keep all existing labels, image, port and every other line unchanged, "
        "and edit the file in place." % manifest
    )
    hints = [
        "Checklist: kubectl get pods identifies CrashLoopBackOff; describe pod shows Last "
        "State OOMKilled, QoS BestEffort and no container limits.",
        "Checklist: kubectl get events and kubectl describe pod share an OOM incident token "
        "and name the reviewed memory limit; kubectl logs corroborates both.",
        "Checklist: insert only the three-line resources/limits/memory block after the image "
        "line in nano; Ctrl+O writes in place and Ctrl+X exits.",
    ]
    return instr, hints, checks


@troubleshoot_template("tls-expiry")
def t_tls_expiry(rng, fs, fixtures, soft):
    """TLS triage: the service loads an archived, expired certificate."""
    svc = tok(rng, "tls", 6)
    host = "%s.internal.example" % svc
    service_ip = ip(rng)
    cn = host
    start_date = "2024-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28))
    expiry_date = "2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28))
    expiry_fragment = tok(rng, "exp", 8)
    verify_fragment = tok(rng, "verify", 8)
    expired_path = "/etc/ssl/%s/archive/expired-%s.crt" % (svc, hexs(rng, 8))
    current_path = "/etc/ssl/%s/current/renewed-%s.crt" % (svc, hexs(rng, 8))
    key_path = "/etc/ssl/%s/private/%s.key" % (svc, svc)
    cfg = "/etc/%s/tls.conf" % svc

    broken_lines = [
        "# %s TLS configuration (managed by ops)" % svc,
        "service_name = %s" % svc,
        "server_name = %s" % host,
        "listen_address = 0.0.0.0:443",
        "certificate_file = %s" % expired_path,
        "private_key_file = %s" % key_path,
        "log_level = info",
    ]
    broken = "\n".join(broken_lines) + "\n"
    fixed = broken.replace(expired_path, current_path)
    wfile(fs, cfg, broken, mode=0o666)
    wfile(fs, expired_path, "\n".join([
        "# certificate fixture metadata; no live TLS is started",
        "subject_CN = %s" % cn,
        "notBefore = %s" % start_date,
        "notAfter = %s" % expiry_date,
        "status = expired",
    ]) + "\n", mode=0o644)
    wfile(fs, current_path, "\n".join([
        "# certificate fixture metadata; no live TLS is started",
        "subject_CN = %s" % cn,
        "notBefore = 2026-01-01",
        "notAfter = 2027-01-01",
        "status = current",
    ]) + "\n", mode=0o644)
    wfile(fs, key_path, "# fixture-only key metadata; no live TLS is started\n", mode=0o600)

    curl_v = "\n".join([
        "# curl -v https://%s/" % host,
        "*   Trying %s:443..." % service_ip,
        "* Connected to %s (%s) port 443" % (host, service_ip),
        "* ALPN: server accepted h2",
        "* Server certificate:",
        "*  subject: CN=%s" % cn,
        "*  start date: %s 00:00:00 GMT" % start_date,
        "*  expire date: %s 00:00:00 GMT" % expiry_date,
        "*  subjectAltName: host \"%s\" matched cert's \"%s\"" % (host, cn),
        "*  issuer: CN=%s" % host,
        "*  SSL certificate verify failed: certificate has expired [%s]"
        % verify_fragment,
        "*  certificate expiry fragment: %s" % expiry_fragment,
        "*  certificate verify detail: expired on %s" % expiry_date,
        "*  certificate reference: %s" % current_path,
        "* Closing connection 0",
    ]) + "\n\n"
    put_fixture_merged(fixtures, "curl_v.txt", curl_v,
                       "tls-expiry", merge_curl_fixtures)
    put_fixture_merged(fixtures, "curl_I.txt", "\n".join([
        "# curl -I https://%s/" % host,
        "curl: (60) SSL certificate problem: certificate has expired",
        "curl: (60) certificate verify failed: expired on %s [%s]"
        % (expiry_date, verify_fragment),
        "requested host: %s" % host,
    ]) + "\n\n", "tls-expiry", merge_curl_fixtures)
    put_fixture_merged(fixtures, "dig.txt", "\n".join([
        "# dig %s A" % host,
        "; <<>> DiG 9.18.1 <<>> %s A" % host,
        ";; global options: +cmd",
        ";; Got answer:",
        ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: %s" % hexs(rng, 4),
        ";; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1",
        "",
        ";; QUESTION SECTION:",
        ";%s. IN A" % host,
        "",
        ";; ANSWER SECTION:",
        "%s. 60 IN A %s" % (host, service_ip),
        "",
        ";; Query time: 2 msec",
        ";; SERVER: 10.0.0.53#53(10.0.0.53)",
    ]) + "\n", "tls-expiry", merge_labelled_reports)
    put_fixture_merged(fixtures, "nc_zv.txt", "\n".join([
        "# nc -zv %s 443" % host,
        "Connection to %s (%s) 443 port [tcp/https] succeeded!" % (host, service_ip),
    ]) + "\n", "tls-expiry", merge_labelled_reports)

    if expired_path == current_path or expired_path not in broken or current_path in broken:
        raise AssertionError("tls-expiry: broken/correct certificate paths are inconsistent")
    if fixed != broken.replace(expired_path, current_path) or fixed == broken:
        raise AssertionError("tls-expiry: repair is not a certificate-path-only change")
    if expiry_date >= "2026-01-01":
        raise AssertionError("tls-expiry: generated certificate is not expired")
    if cn not in curl_v or expiry_date not in curl_v or expiry_fragment not in curl_v:
        raise AssertionError("tls-expiry: curl -v lacks certificate answer evidence")
    if current_path not in curl_v or verify_fragment not in curl_v:
        raise AssertionError("tls-expiry: curl -v lacks verification/reference clues")
    if host not in fixtures["dig.txt"] or service_ip not in fixtures["dig.txt"]:
        raise AssertionError("tls-expiry: DNS does not resolve the certificate host")
    if "succeeded" not in fixtures["nc_zv.txt"] or host not in fixtures["nc_zv.txt"]:
        raise AssertionError("tls-expiry: nc does not show the TLS port open")
    if "certificate has expired" not in fixtures["curl_I.txt"]:
        raise AssertionError("tls-expiry: curl -I lacks the certificate failure")

    certificate_answer = "%s:%s:%s" % (cn, expiry_date, expiry_fragment)
    checks = [
        hash_check("tls-expiry-1", certificate_answer,
                   "Submit the certificate CN, expiry date and expiry fragment from "
                   "`curl -v`, joined by colons in that order (CN:YYYY-MM-DD:fragment)."),
        hash_check("tls-expiry-2", current_path,
                   "Submit the current certificate path revealed by the `curl -v` "
                   "certificate reference line."),
        file_check("tls-expiry-3", cfg, fixed, "certificate-" + svc,
                   "Repair %s in place: change only `certificate_file` to the current "
                   "certificate path revealed by `curl -v`." % cfg,
                   initial=broken),
    ]
    instr = (
        "An HTTPS service fails certificate verification even though DNS resolves its name "
        "and the TCP port accepts connections. Inspect its TLS config at %s, then use "
        "`curl -v <https-url>`, `curl -I <https-url>`, `dig <name> A` and "
        "`nc -zv <host> 443` to separate name resolution and transport from certificate "
        "validation. The verbose output names the certificate identity, expiry evidence "
        "and a current certificate reference.\n\n"
        "The checked config is %s. Its corrected end state is byte-for-byte the shipped "
        "file with exactly one change: `certificate_file` becomes the current certificate "
        "path named by the verbose evidence. Keep the server name, listener, key path, "
        "comment and every other byte unchanged, and edit the file in place."
        % (cfg, cfg)
    )
    hints = [
        "Checklist: curl -v shows whether TCP connected before TLS verification failed; "
        "read subject CN, expire date and the certificate expiry fragment in that report.",
        "Checklist: dig confirms the requested name resolves and nc -zv confirms the port "
        "is open; curl -I corroborates that certificate validation, not DNS or TCP, failed.",
        "Checklist: the curl -v certificate reference names the current certificate file; "
        "replace only certificate_file in nano, then Ctrl+O writes and Ctrl+X exits.",
    ]
    return instr, hints, checks


@troubleshoot_template("dependency-down")
def t_dependency_down(rng, fs, fixtures, soft):
    """Application triage: a reachable dependency refuses the configured port."""
    svc = tok(rng, "appsvc", 6)
    app_host = "%s.internal.example" % svc
    app_unit = svc + ".service"
    dependency = tok(rng, "cache", 6)
    dependency_unit = dependency + ".service"
    dependency_process = dependency + "-server"
    correct_host = "%s.internal.example" % dependency
    wrong_host = "retired-%s.internal.example" % hexs(rng, 8)
    correct_port = rng.randint(18000, 28999)
    wrong_port = rng.randint(29000, 39999)
    incident = tok(rng, "dep", 8)
    app_ip = ip(rng)
    dependency_ip = ip(rng)
    cfg = "/etc/%s/%s.conf" % (svc, svc)

    broken_lines = [
        "# %s application configuration (managed by ops)" % svc,
        "service_name = %s" % svc,
        "dependency_unit = %s" % dependency_unit,
        "dependency_host = %s" % wrong_host,
        "dependency_port = %d" % wrong_port,
        "connect_timeout_ms = 2500",
        "retry_count = 3",
        "log_level = warn",
    ]
    broken = "\n".join(broken_lines) + "\n"
    fixed_lines = list(broken_lines)
    fixed_lines[3] = "dependency_host = %s" % correct_host
    fixed_lines[4] = "dependency_port = %d" % correct_port
    fixed = "\n".join(fixed_lines) + "\n"
    wfile(fs, cfg, broken, mode=0o666)

    status = "\n".join([
        "# systemctl status %s" % dependency_unit,
        "* %s - %s dependency" % (dependency_unit, dependency),
        "     Loaded: loaded (/etc/systemd/system/%s; enabled)" % dependency_unit,
        "     Active: failed (Result: exit-code) since 4min ago",
        "   Main PID: %d (code=exited, status=1/FAILURE)" % rng.randint(1200, 9000),
        "",
        "Environment=\"DEPENDENCY_HOST=%s\" \"DEPENDENCY_PORT=%d\""
        % (correct_host, correct_port),
        "%s %s[%d]: FATAL downstream listener unavailable; incident %s"
        % (ts(rng), dependency_process, rng.randint(1200, 9000), incident),
        "%s systemd[1]: %s: Failed with result 'exit-code'."
        % (ts(rng), dependency_unit),
    ]) + "\n"
    put_fixture_merged(fixtures, "systemctl_status.txt", status,
                       "dependency-down", merge_labelled_reports)
    journal = "\n".join([
        "# journalctl -u %s -n 100 --no-pager" % app_unit,
        "-- unit %s --" % app_unit,
        "%s %s[%d]: ERROR downstream %s at %s:%d failed: Connection refused"
        % (ts(rng), svc, rng.randint(1200, 9000), dependency_unit,
           wrong_host, wrong_port),
        "%s %s[%d]: dependency incident %s; listener recovered on approved endpoint %s:%d"
        % (ts(rng), svc, rng.randint(1200, 9000), incident,
           correct_host, correct_port),
        "%s systemd[1]: %s: Main process exited, code=exited, status=1/FAILURE"
        % (ts(rng), app_unit),
        "%s systemd[1]: %s: Failed with result 'exit-code'." % (ts(rng), app_unit),
    ]) + "\n"
    put_fixture_merged(fixtures, "journalctl_unit.txt", journal,
                       "dependency-down", merge_labelled_reports)
    put_fixture_merged(fixtures, "curl_v.txt", "\n".join([
        "# curl -v http://%s:8080/" % app_host,
        "*   Trying %s:8080..." % app_ip,
        "< HTTP/1.1 502 Bad Gateway",
        "< Server: %s" % svc,
        "< X-Downstream-Unit: %s" % dependency_unit,
        "< X-Downstream-Target: %s:%d" % (wrong_host, wrong_port),
        "< Content-Length: 0",
        "* Closing connection 0",
    ]) + "\n\n", "dependency-down", merge_curl_fixtures)
    put_fixture_merged(fixtures, "ss_tlnp.txt", "\n".join([
        "State   Recv-Q  Send-Q  Local Address:Port   Peer Address:Port  Process",
        'LISTEN  0       128     0.0.0.0:%-11d 0.0.0.0:*          users:(("%s",pid=%d,fd=8))'
        % (correct_port, dependency_process, rng.randint(1200, 9000)),
        'LISTEN  0       128     127.0.0.1:22       0.0.0.0:*          users:(("sshd",pid=411,fd=3))',
    ]) + "\n", "dependency-down", merge_ss_fixtures)
    put_fixture_merged(fixtures, "ping.txt", "\n".join([
        "# ping %s" % correct_host,
        "PING %s (%s) 56(84) bytes of data." % (correct_host, dependency_ip),
        "64 bytes from %s (%s): icmp_seq=1 ttl=62 time=2.4 ms"
        % (correct_host, dependency_ip),
        "64 bytes from %s (%s): icmp_seq=2 ttl=62 time=2.1 ms"
        % (correct_host, dependency_ip),
        "",
        "--- %s ping statistics ---" % correct_host,
        "2 packets transmitted, 2 received, 0% packet loss, time 1001ms",
    ]) + "\n", "dependency-down", merge_labelled_reports)

    if correct_host == wrong_host or correct_port == wrong_port:
        raise AssertionError("dependency-down: correct/wrong endpoints are not distinct")
    if wrong_host not in broken or correct_host in broken \
            or str(wrong_port) not in broken or str(correct_port) in broken:
        raise AssertionError("dependency-down: broken config does not contain only the wrong endpoint")
    if fixed != broken.replace(wrong_host, correct_host).replace(
            "dependency_port = %d" % wrong_port,
            "dependency_port = %d" % correct_port) or fixed == broken:
        raise AssertionError("dependency-down: repair is not exactly host plus port")
    if incident not in status or incident not in journal or dependency_unit not in status:
        raise AssertionError("dependency-down: unit/fragment evidence is missing")
    if "Connection refused" not in journal or wrong_host not in journal \
            or str(wrong_port) not in journal:
        raise AssertionError("dependency-down: journal does not show the refused configured endpoint")
    if correct_host not in journal or str(correct_port) not in journal:
        raise AssertionError("dependency-down: journal does not reveal the approved endpoint")
    if str(correct_port) not in fixtures["ss_tlnp.txt"] \
            or str(wrong_port) in fixtures["ss_tlnp.txt"]:
        raise AssertionError("dependency-down: ss evidence has the wrong listening-port state")
    if "502 Bad Gateway" not in fixtures["curl_v.txt"]:
        raise AssertionError("dependency-down: curl evidence lacks the application's 502")
    if correct_host not in fixtures["ping.txt"] or "0% packet loss" not in fixtures["ping.txt"]:
        raise AssertionError("dependency-down: ping does not corroborate dependency reachability")

    dependency_answer = "%s:%s" % (dependency_unit, incident)
    checks = [
        hash_check("dependency-down-1", dependency_answer,
                   "Submit the dependency unit name and incident fragment, joined by a "
                   "colon (unit first), as shown by systemctl status and journalctl -u."),
        hash_check("dependency-down-2", str(correct_port),
                   "Submit the dependency's listening port from `ss -tlnp`, corroborated "
                   "by the approved endpoint in the status/journal evidence.",
                   plain=str(correct_port)),
        file_check("dependency-down-3", cfg, fixed, "dependency-" + svc,
                   "Repair %s in place: change only `dependency_host` and `dependency_port` "
                   "to the approved dependency endpoint revealed by the evidence." % cfg,
                   initial=broken),
    ]
    instr = (
        "The application unit %s returns 502 because its downstream dependency refuses the "
        "configured connection. Use `systemctl status <dependency-unit>`, "
        "`journalctl -u %s -n 100 --no-pager`, `curl -v <app-url>` and `ss -tlnp` to "
        "identify the failed dependency, its incident fragment, the refused configured "
        "endpoint and the approved listening endpoint. Use `ping <dependency-host>` to "
        "confirm host reachability separately from service availability.\n\n"
        "The checked config is %s. Its corrected end state is byte-for-byte the shipped "
        "file with exactly two changes: `dependency_host` and `dependency_port` become the "
        "approved dependency endpoint. Keep the dependency unit, service name, timeout, "
        "retry count, log level and every other byte unchanged, and edit the file in place."
        % (app_unit, app_unit, cfg)
    )
    hints = [
        "Checklist: systemctl status shows the failed dependency unit, environment and "
        "incident fragment; journalctl -u shows the application's Connection refused.",
        "Checklist: curl -v exposes the application's 502 and downstream target; ss -tlnp "
        "shows which dependency port is actually listening, and ping tests host reachability.",
        "Checklist: edit only dependency_host and dependency_port in nano to the approved "
        "endpoint; Ctrl+O writes in place and Ctrl+X exits.",
    ]
    return instr, hints, checks


@troubleshoot_template("disk-full")
def t_disk_full(rng, fs, fixtures, soft):
    """Resource exhaustion triage: a full filesystem, a runaway log directory
    and a logrotate policy that never compresses or expires rotated logs."""
    svc = tok(rng, "svc", 6)                 # random service id, shared by all paths
    logdir = "/var/log/%s" % svc
    rot = "/etc/logrotate.d/%s" % svc

    # Only a filesystem that actually backs the runaway log directory can
    # plausibly be the one at 100%, so the choice is /var or a dedicated
    # /var/log volume; both keep /var/log/<svc> as the biggest directory.
    full_mount = rng.choice(["/var", "/var", "/var/log"])
    df_rows = [
        ("/dev/sda2", "40G", "18G", "20G", "48%", "/"),
        ("tmpfs", "3.9G", "1.4M", "3.9G", "1%", "/run"),
        ("/dev/sda1", "512M", "182M", "304M", "38%", "/boot"),
    ]
    if full_mount == "/var":
        df_rows.append(("/dev/mapper/vg0-var", "200G", "200G", "0", "100%", "/var"))
        df_rows.append(("/dev/mapper/vg0-home", "500G", "180G", "294G", "38%", "/home"))
    else:
        df_rows.append(("/dev/mapper/vg0-var", "120G", "52G", "62G", "46%", "/var"))
        df_rows.append(("/dev/mapper/vg0-log", "60G", "60G", "0", "100%", "/var/log"))
        df_rows.append(("/dev/mapper/vg0-home", "500G", "180G", "294G", "38%", "/home"))
    df_lines = ["Filesystem               Size  Used Avail Use% Mounted on"]
    for dev, size, used, avail, pct, mnt in df_rows:
        df_lines.append("%-24s %4s  %4s  %4s %4s %s" % (dev, size, used, avail, pct, mnt))
    put_fixture(fixtures, "df_h.txt", "\n".join(df_lines) + "\n", "disk-full")

    # du -sh /var/log/* | sort -h: ascending sizes, the runaway dir is last.
    decoys = [
        ("8.0K", "/var/log/private"),
        ("1.2M", "/var/log/alternatives.log"),
        ("14M", "/var/log/apt"),
        ("96M", "/var/log/auth.log"),
        ("420M", "/var/log/syslog"),
        ("2.1G", "/var/log/journal"),
    ]
    big_size = rng.choice(["38G", "47G", "52G", "61G", "73G"])
    du_lines = ["%s\t%s" % (size, path) for size, path in decoys]
    du_lines.append("%s\t%s" % (big_size, logdir))
    put_fixture(fixtures, "du_sh.txt", "\n".join(du_lines) + "\n", "disk-full")

    total = rng.randint(15000, 17000)
    used = rng.randint(3000, 5000)
    cache = rng.randint(1500, 2600)
    free = total - used - cache
    avail = free + cache // 2
    shared = rng.randint(80, 400)
    put_fixture_merged(fixtures, "free_m.txt", "# snapshot disk-full\n" + "\n".join([
        "               total        used        free      shared  buff/cache   available",
        "Mem:        %8d    %8d    %8d    %8d    %8d    %8d"
        % (total, used, free, shared, cache, avail),
        "Swap:       %8d    %8d    %8d" % (4096, 512, 3584),
    ]) + "\n", "disk-full", merge_reports)

    pid = rng.randint(1200, 32000)
    load = "%.2f, %.2f, %.2f" % (
        rng.uniform(0.1, 1.5), rng.uniform(0.1, 1.8), rng.uniform(0.1, 2.0))
    put_fixture_merged(fixtures, "top.txt", "# snapshot disk-full\n" + "\n".join([
        "top - %02d:%02d:%02d up 12 days,  3:14,  1 user,  load average: %s"
        % (rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59), load),
        "Tasks: 128 total,   1 running, 127 sleeping,   0 stopped,   0 zombie",
        "%Cpu(s):  3.1 us,  1.2 sy,  0.0 ni, 95.4 id,  0.3 wa,  0.0 hi,  0.0 si,  0.0 st",
        "MiB Mem :  %8.1f total, %8.1f free, %8.1f used, %8.1f buff/cache"
        % (total, free, used, cache),
        "MiB Swap:   4096.0 total,   3584.0 free,    512.0 used.  %8.1f avail Mem" % avail,
        "",
        "    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND",
        "  %5d %-8s  20   0  812344  98760  12344 S   2.3   0.6   1:23.45 %s-agent"
        % (pid, svc, svc),
    ]) + "\n", "disk-full", merge_reports)

    # The service journal is a soft fixture: svc-crashloop also owns
    # journalctl_unit.txt, and a hard fixture must win in either build order.
    journal = [
        "%s %s[%d]: ERROR failed to write %s/app.log: No space left on device"
        % (ts(rng), svc, pid, logdir)
        for _ in range(rng.randint(4, 8))
    ]
    journal.append("%s systemd[1]: %s.service: failed to write to journal: "
                   "No space left on device" % (ts(rng), svc))
    put_soft(soft, fixtures, "journalctl_unit.txt", "\n".join(journal) + "\n", "disk-full")

    # Real files so the drill is tangible: the log directory and the
    # (deliberately 0666) logrotate policy the trainee must repair in place.
    wfile(fs, logdir + "/app.log", "\n".join(
        "%s %s[%d]: INFO request served" % (ts(rng), svc, pid) for _ in range(20)
    ) + "\n")
    rot_broken = "\n".join([
        "%s/*.log {" % logdir,
        "    daily",
        "    missingok",
        "    notifempty",
        "    copytruncate",
        "}",
    ]) + "\n"
    rot_fixed = "\n".join([
        "%s/*.log {" % logdir,
        "    daily",
        "    rotate 7",
        "    compress",
        "    missingok",
        "    notifempty",
        "    copytruncate",
        "}",
    ]) + "\n"
    # 0666 so the unprivileged trainee can rewrite it in place (like the
    # svc-crashloop config); the directory stays root-owned.
    wfile(fs, rot, rot_broken, mode=0o666)

    # Load-bearing invariants: exactly one filesystem is full, the biggest du
    # entry is the runaway log dir, and the broken policy really lacks the fix.
    if not any(row[4] == "100%" and row[5] == full_mount for row in df_rows):
        raise AssertionError("disk-full: 100%% row is not on %s" % full_mount)
    if sum(" 100% " in line for line in df_lines) != 1:
        raise AssertionError("disk-full: expected exactly one filesystem at 100%")
    if not du_lines[-1].endswith(logdir):
        raise AssertionError("disk-full: du output does not end on the runaway dir")
    if "compress" in rot_broken or "rotate 7" in rot_broken:
        raise AssertionError("disk-full: broken logrotate already contains the fix")
    if rot_fixed == rot_broken:
        raise AssertionError("disk-full: repaired logrotate equals the broken bytes")

    checks = [
        hash_check("disk-full-1", full_mount,
                   "Submit the full mount point (the 'Mounted on' column) of the "
                   "filesystem sitting at 100% in df -h.", plain=full_mount),
        hash_check("disk-full-2", svc,
                   "Submit the full path of the largest directory listed by "
                   "du -sh /var/log/* | sort -h; its name ends with the service id.",
                   plain=logdir),
        file_check("disk-full-3", rot, rot_fixed, "logrotate-" + svc,
                   "Repair %s in place: add `rotate 7` right after `daily`, then `compress` "
                   "right after that, changing nothing else." % rot,
                   initial=rot_broken),
    ]
    instr = (
        "A host has run out of disk space; application writes fail with 'No space left on "
        "device'. Triage it with the standard checklist: `df -h` shows which mounted "
        "filesystem is at 100%%, `du -sh /var/log/* | sort -h` shows which log directory is "
        "largest, and `free -m` / `top` give memory and CPU pressure for context.\n\n"
        "The logs are filling the disk because the logrotate policy at %s never compresses "
        "or expires rotated files. Its corrected end state is byte-for-byte the shipped file "
        "with exactly two lines added: `    rotate 7` immediately after the `daily` line, and "
        "`    compress` immediately after that. Leave the log path, the other options and the "
        "closing brace unchanged, and edit the file in place." % rot
    )
    hints = [
        "Checklist: df -h lists every mount with its Use%; the one at 100% is the "
        "partition that filled up (its 'Mounted on' value is the answer).",
        "Checklist: du -sh /var/log/* | sort -h orders directories by size; the largest is "
        "the runaway log directory and its name carries the service id.",
        "Checklist: in nano, add the two option lines, Ctrl+O writes in place and Ctrl+X "
        "exits; `cat %s` confirms the final bytes." % rot,
    ]
    return instr, hints, checks


@troubleshoot_template("container-crash")
def t_container_crash(rng, fs, fixtures, soft):
    """Container troubleshooting: an app container exits 1 because a required
    environment variable is unset in its .env file."""
    name = tok(rng, "api", 6)                # container name, also the env basename
    digest = hexs(rng, 12)                    # 12-hex container id
    tag = "%d.%d.%d" % (rng.randint(1, 4), rng.randint(0, 9), rng.randint(0, 9))
    image = "registry.example/%s:%s" % (name, tag)
    secret = tok(rng, "cfg", 8)              # random config fragment printed in logs
    missing_var = "SVC_KEY_" + hexs(rng, 4).upper()
    exit_code = 1
    envf = "/home/trainee/containers/%s.env" % name
    created = "%d minutes ago" % rng.randint(3, 25)
    finished = "%d minutes ago" % rng.randint(1, 4)

    env_broken = "\n".join([
        "# environment for %s (managed by ops)" % name,
        "LOG_LEVEL=info",
        "PORT=8080",
    ]) + "\n"
    env_fixed = env_broken + "%s=%s\n" % (missing_var, secret)
    wfile(fs, envf, env_broken, mode=0o644)

    status = "Exited (%d) %s" % (exit_code, finished)
    docker_ps = "\n".join([
        "CONTAINER ID   IMAGE                                 COMMAND                  "
        "CREATED          STATUS                      PORTS     NAMES",
        "%s   %-36s  \"/usr/local/bin/entry…\"   %-15s  %-26s            %s"
        % (digest, image, created, status, name),
    ]) + "\n"
    put_fixture_merged(fixtures, "docker_ps_a.txt", docker_ps,
                       "container-crash", merge_container_ps_fixtures)
    podman_ps = "\n".join([
        "CONTAINER ID  IMAGE                                 COMMAND               "
        "CREATED         STATUS                      PORTS       NAMES",
        "%s  %-36s  /usr/local/bin/entry  %-14s  %-26s              %s"
        % (digest, image, created, status, name),
    ]) + "\n"
    put_fixture_merged(fixtures, "podman_ps_a.txt", podman_ps,
                       "container-crash", merge_container_ps_fixtures)

    logs = "# container %s (container-crash)\n%s" % (name, "\n".join([
        "%s %s: starting %s %s (build %s)" % (ts(rng), name, name, tag, secret),
        "%s %s: FATAL: required environment variable %s is not set (config %s); "
        "refusing to start" % (ts(rng), name, missing_var, secret),
        "%s %s: exiting with status %d" % (ts(rng), name, exit_code),
    ]) + "\n")
    put_fixture_merged(fixtures, "docker_logs.txt", logs,
                       "container-crash", merge_reports)
    put_fixture_merged(fixtures, "podman_logs.txt", logs,
                       "container-crash", merge_reports)

    inspect_docker = (
        '[{"Id":"%s","Name":"/%s","Config":{"Image":"%s",'
        '"Env":["LOG_LEVEL=info","PORT=8080"]},'
        '"State":{"Status":"exited","ExitCode":%d,"Error":"","OOMKilled":false},'
        '"Labels":{"config-id":"%s"}}]'
        % (digest, name, image, exit_code, secret)
    )
    put_fixture_merged(fixtures, "docker_inspect.txt", inspect_docker + "\n",
                       "container-crash", merge_inspect_fixtures)
    inspect_podman = (
        '[{"Id":"%s","Name":"%s","Image":"%s","Config":{"Env":["LOG_LEVEL=info",'
        '"PORT=8080"]},"State":{"Status":"exited","ExitCode":%d,"OOMKilled":false,'
        '"Error":""},"Labels":{"config-id":"%s"}}]'
        % (digest, name, image, exit_code, secret)
    )
    put_fixture_merged(fixtures, "podman_inspect.txt", inspect_podman + "\n",
                       "container-crash", merge_inspect_fixtures)

    # Load-bearing invariants: the break is real and each clue is discoverable.
    if missing_var in env_broken or secret in env_broken:
        raise AssertionError("container-crash: broken env file already solved")
    if name not in docker_ps or ("Exited (%d)" % exit_code) not in docker_ps:
        raise AssertionError("container-crash: docker ps missing name/exit status")
    if ("Exited (%d)" % exit_code) not in podman_ps:
        raise AssertionError("container-crash: podman ps missing exit status")
    if missing_var not in fixtures["docker_logs.txt"]:
        raise AssertionError("container-crash: missing variable not named in logs")
    if secret not in fixtures["docker_logs.txt"]:
        raise AssertionError("container-crash: config fragment not in logs")
    if missing_var in fixtures["docker_inspect.txt"]:
        raise AssertionError("container-crash: missing var must not be in inspect Env")
    if env_fixed == env_broken:
        raise AssertionError("container-crash: repaired env equals the broken bytes")

    name_answer = "%s:%d" % (name, exit_code)
    checks = [
        hash_check("container-crash-1", name,
                   "Submit the container name and its exit code from `docker ps -a` (or "
                   "`podman ps -a`), joined by a colon (name first).", plain=name_answer),
        hash_check("container-crash-2", missing_var,
                   "Submit the environment variable the container logs say is not set.",
                   plain=missing_var),
        file_check("container-crash-3", envf, env_fixed, secret,
                   "Repair %s: append the line `<missing-variable>=%s`, where the variable "
                   "name is the one the logs report missing. Leave every existing line "
                   "unchanged." % (envf, secret), initial=env_broken),
    ]
    instr = (
        "A container is crash-looping: it starts and exits almost immediately with a "
        "non-zero exit code because a required environment variable is unset. Triage it "
        "with the container tooling: `docker ps -a` (or `podman ps -a`) shows the container "
        "name and its `Exited (N)` status, `docker logs <name>` (or `podman logs <name>`) "
        "shows the fatal config error, and `docker inspect <name>` / `podman inspect <name>` "
        "shows the container's environment and exit code.\n\n"
        "The environment file to repair is %s. Its corrected end state is byte-for-byte the "
        "shipped file with exactly one line appended at the end: `<missing-variable>=%s`, "
        "where <missing-variable> is the variable the logs say is not set and the value is "
        "exactly `%s`. Leave every existing line unchanged and edit the file in place."
        % (envf, secret, secret)
    )
    hints = [
        "Checklist: docker ps -a / podman ps -a shows STATUS 'Exited (N)'; N is the exit "
        "code and the NAMES column is the container name.",
        "Checklist: docker logs <name> / podman logs <name> prints the FATAL line naming the "
        "unset variable; docker inspect / podman inspect shows Config.Env without that key.",
        "Checklist: append the corrected line to the .env in place with nano (Ctrl+O writes, "
        "Ctrl+X exits) and confirm the exact bytes with cat.",
    ]
    return instr, hints, checks


@troubleshoot_template("nginx-config")
def t_nginx_config(rng, fs, fixtures, soft):
    """Web-server triage: parse the nginx error, inspect ownership metadata and
    repair both the unterminated directive and the stale virtual-host name."""
    site = tok(rng, "site", 6)
    cfg = "/etc/nginx/conf.d/%s.conf" % site
    reference = "/etc/nginx/references/%s.conf" % tok(rng, "ref", 8)
    listen_port = rng.choice([80, 8080, 8443])
    server_name = "web-%s.internal.example" % hexs(rng, 8)
    wrong_server_name = "legacy-%s.internal.example" % hexs(rng, 8)

    fixed_lines = [
        "server {",
        "    listen %d;" % listen_port,
        "    server_name %s;" % server_name,
        "    root /srv/www/%s;" % site,
        "    access_log /var/log/nginx/%s.access.log;" % site,
        "    location / {",
        "        try_files $uri $uri/ =404;",
        "    }",
        "}",
    ]
    # Keep the server_name repair separate from the syntax repair so the brief
    # can require exactly two changed lines on every seed.
    bad_index = rng.choice([1, 3, 4, 6])
    broken_lines = list(fixed_lines)
    broken_lines[2] = fixed_lines[2].replace(server_name, wrong_server_name)
    broken_lines[bad_index] = fixed_lines[bad_index][:-1]  # remove the semicolon
    broken = "\n".join(broken_lines) + "\n"
    fixed = "\n".join(fixed_lines) + "\n"
    error_line = bad_index + 1
    directive = fixed_lines[bad_index].strip().split()[0]
    error_fragment = "nginx-L%d-%s" % (error_line, hexs(rng, 6))

    # 0666 is intentional: /etc/nginx stays root-owned, but the trainee has no
    # sudo and must still be able to repair this one checked file in place.
    wfile(fs, cfg, broken, mode=0o666)
    reference_text = "\n".join([
        "# canonical nginx reference for the web service",
        "server {",
        "    listen 80;",
        "    server_name %s;" % server_name,
        "}",
    ]) + "\n"
    wfile(fs, reference, reference_text, mode=0o644)

    put_fixture(fixtures, "nginx_t.txt", "\n".join([
        'nginx: [emerg] directive "%s" is not terminated by ";" in %s:%d'
        % (directive, cfg, error_line),
        "nginx: [emerg] syntax-check-id=%s" % error_fragment,
        "nginx: configuration file /etc/nginx/nginx.conf test failed",
    ]) + "\n", "nginx-config")
    # These are deliberately non-load-bearing: a permissive/disabled SELinux
    # state and an empty AVC search provide realistic context, not the cause.
    # They are soft claims so selinux-avc owns the same command fixtures when
    # both stages are selected; nginx-config still gets its benign context alone.
    put_soft(soft, fixtures, "getenforce.txt",
             rng.choice(["Permissive", "Disabled"]) + "\n", "nginx-config")
    put_soft(soft, fixtures, "ausearch_avc.txt", "----\n<no matches>\n", "nginx-config")

    # Load-bearing invariants: the reported line is the broken line, the correct
    # virtual-host name is absent from the shipped config, and exactly two lines
    # separate the broken and corrected byte strings.
    changed = [i for i, (a, b) in enumerate(zip(broken_lines, fixed_lines)) if a != b]
    if changed != sorted([bad_index, 2]):
        raise AssertionError("nginx-config: expected exactly the syntax and server_name changes")
    if wrong_server_name not in broken or server_name in broken:
        raise AssertionError("nginx-config: stale/canonical server_name state inconsistent")
    if server_name not in reference_text:
        raise AssertionError("nginx-config: canonical server_name missing from reference")
    if cfg not in fixtures["nginx_t.txt"] or (":%d" % error_line) not in fixtures["nginx_t.txt"]:
        raise AssertionError("nginx-config: nginx -t does not report the shipped path and line")
    if error_fragment not in fixtures["nginx_t.txt"]:
        raise AssertionError("nginx-config: syntax diagnostic fragment missing")

    checks = [
        hash_check("nginx-config-1", error_fragment,
                   "Submit the complete syntax-check token reported by `nginx -t`."),
        hash_check("nginx-config-2", "nginx",
                   "Submit the owner name shown by `ls -l` on the reference config."),
        file_check("nginx-config-3", cfg, fixed, "nginx-" + site,
                   "Repair %s in place: add the missing semicolon on the directive "
                   "reported by `nginx -t` and replace only `server_name` with the "
                   "canonical value from the reference config." % cfg,
                   initial=broken),
    ]
    instr = (
        "The nginx site at %s fails its configuration test. Start with `nginx -t`: it "
        "names the unterminated directive, its line number and a syntax-check token. "
        "Then run `ls -l %s` to identify the service account that owns the canonical "
        "reference config, and read it with `cat` to recover the expected `server_name`.\n\n"
        "The checked config is %s. Its corrected end state is byte-for-byte the shipped "
        "file with exactly two changes: terminate the directive named by `nginx -t` with "
        "a semicolon, and replace only the `server_name` value with the canonical value "
        "from the reference config. Leave the port, root, log path, location block and "
        "all other bytes unchanged."
        % (cfg, reference, cfg)
    )
    hints = [
        "Checklist: nginx -t is authoritative for the parse failure; copy its complete "
        "syntax-check token. getenforce and ausearch -m avc are context only here.",
        "Checklist: ls -l <reference-config> shows the owning service account in the "
        "owner column; submit that owner name, not the group.",
        "Checklist: cat <reference-config> exposes the canonical server_name. In nano, "
        "add only the reported semicolon and replace only server_name; Ctrl+O writes "
        "the checked file in place.",
    ]
    return instr, hints, checks


@troubleshoot_template("dns-mismatch")
def t_dns_mismatch(rng, fs, fixtures, soft):
    """DNS/endpoint triage: the app bypasses its canonical service name and
    points directly at a stale backend that returns a gateway failure."""
    service = tok(rng, "api", 6)
    correct_host = "%s.internal.example" % service
    wrong_host = "stale-%s.edge.example" % hexs(rng, 8)
    wrong_ip = ip(rng)
    backend_port = rng.choice([8080, 8443, 9443])
    connection_fragment = "conn-%s" % hexs(rng, 8)
    response_fragment = "edge-%s" % hexs(rng, 6)
    endpoint = "/home/trainee/app/endpoint.conf"

    broken = "\n".join([
        "# application endpoint (managed by ops)",
        "endpoint_url=http://%s:%d/v1" % (wrong_host, backend_port),
        "connect_timeout_ms=2500",
        "retry_count=3",
    ]) + "\n"
    fixed = broken.replace(wrong_host, correct_host)
    wfile(fs, endpoint, broken, mode=0o644)

    # The canonical service hostname is the DNS owner/query name.  Its current
    # CNAME target is the stale backend hard-coded into the app config.
    put_fixture_merged(fixtures, "dig.txt", "\n".join([
        "# dig %s A" % correct_host,
        "; <<>> DiG 9.18.1 <<>> %s A" % correct_host,
        ";; global options: +cmd",
        ";; Got answer:",
        ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: %s" % hexs(rng, 4),
        ";; flags: qr rd ra; QUERY: 1, ANSWER: 2, AUTHORITY: 0, ADDITIONAL: 1",
        "",
        ";; QUESTION SECTION:",
        ";%s. IN A" % correct_host,
        "",
        ";; ANSWER SECTION:",
        "%s. 60 IN CNAME %s." % (correct_host, wrong_host),
        "%s. 60 IN A %s" % (wrong_host, wrong_ip),
        "",
        ";; Query time: 3 msec",
        ";; SERVER: 10.0.0.53#53(10.0.0.53)",
        ";; WHEN: %s" % ts(rng),
        ";; MSG SIZE  rcvd: %d" % rng.randint(80, 140),
    ]) + "\n", "dns-mismatch", merge_labelled_reports)
    put_fixture(fixtures, "nslookup.txt", "\n".join([
        "Server:        10.0.0.53",
        "Address:       10.0.0.53#53",
        "",
        "Non-authoritative answer:",
        "%s canonical name = %s." % (correct_host, wrong_host),
        "Name:   %s" % wrong_host,
        "Address: %s" % wrong_ip,
    ]) + "\n", "dns-mismatch")
    put_fixture_merged(fixtures, "curl_I.txt", "\n".join([
        "# curl -I http://%s:%d/v1" % (wrong_host, backend_port),
        "HTTP/1.1 502 Bad Gateway",
        "Server: %s" % response_fragment,
        "Date: %s" % ts(rng),
        "X-Backend-Host: %s" % wrong_host,
        "X-Upstream: %s:%d" % (wrong_ip, backend_port),
        "Content-Length: 0",
        "Connection: keep-alive",
    ]) + "\n\n", "dns-mismatch", merge_curl_fixtures)
    put_fixture_merged(fixtures, "curl_v.txt", "\n".join([
        "# curl -v http://%s:%d/v1" % (wrong_host, backend_port),
        "*   Trying %s:%d..." % (wrong_ip, backend_port),
        "* connect to %s (%s) port %d failed: Connection refused"
        % (wrong_host, wrong_ip, backend_port),
        "* connection %s: refused" % connection_fragment,
        "* Closing connection 0",
    ]) + "\n\n", "dns-mismatch", merge_curl_fixtures)
    put_fixture_merged(fixtures, "ping.txt", "\n".join([
        "# ping %s" % wrong_host,
        "PING %s (%s) 56(84) bytes of data." % (wrong_host, wrong_ip),
        "64 bytes from %s (%s): icmp_seq=1 ttl=62 time=2.4 ms"
        % (wrong_host, wrong_ip),
        "64 bytes from %s (%s): icmp_seq=2 ttl=62 time=2.1 ms"
        % (wrong_host, wrong_ip),
        "",
        "--- %s ping statistics ---" % wrong_host,
        "2 packets transmitted, 2 received, 0% packet loss, time 1001ms",
    ]) + "\n", "dns-mismatch", merge_labelled_reports)
    put_fixture_merged(fixtures, "nc_zv.txt", "\n".join([
        "# nc -zv %s %d" % (wrong_host, backend_port),
        "nc: connect to %s (%s) port %d (tcp) failed: Connection refused"
        % (wrong_host, wrong_ip, backend_port),
        "connection %s: refused" % connection_fragment,
    ]) + "\n", "dns-mismatch", merge_labelled_reports)

    if correct_host == wrong_host or wrong_host not in broken or correct_host in broken:
        raise AssertionError("dns-mismatch: canonical/stale endpoint state inconsistent")
    if fixed != broken.replace(wrong_host, correct_host) or fixed == broken:
        raise AssertionError("dns-mismatch: endpoint repair is not a hostname-only change")
    for name in ("dig.txt", "nslookup.txt"):
        if correct_host not in fixtures[name] or wrong_host not in fixtures[name]:
            raise AssertionError("dns-mismatch: %s lacks canonical/stale host evidence" % name)
    if "502" not in fixtures["curl_I.txt"] or wrong_host not in fixtures["curl_I.txt"]:
        raise AssertionError("dns-mismatch: curl -I lacks the stale-host gateway failure")
    if connection_fragment not in fixtures["curl_v.txt"]:
        raise AssertionError("dns-mismatch: curl -v connection fragment missing")
    if "Connection refused" not in fixtures["nc_zv.txt"] \
            or connection_fragment not in fixtures["nc_zv.txt"]:
        raise AssertionError("dns-mismatch: nc evidence/fragment inconsistent")
    if wrong_ip not in fixtures["ping.txt"]:
        raise AssertionError("dns-mismatch: ping does not corroborate the stale address")

    checks = [
        hash_check("dns-mismatch-1", correct_host,
                   "Submit the canonical service hostname shown as the DNS query/answer "
                   "owner, not the stale CNAME target."),
        hash_check("dns-mismatch-2", connection_fragment,
                   "Submit the connection diagnostic token printed by `curl -v` or "
                   "`nc -zv`."),
        file_check("dns-mismatch-3", endpoint, fixed, "dns-" + service,
                   "Repair %s in place: replace only the hostname in `endpoint_url` with "
                   "the canonical service hostname from the DNS evidence." % endpoint,
                   initial=broken),
    ]
    instr = (
        "The application endpoint at %s is returning 502. Triage name resolution and "
        "transport with `dig <name> A`, `nslookup <name>`, `curl -I <url>`, "
        "`curl -v <url>`, `ping <host>` and `nc -zv <host> <port>`. Distinguish the "
        "canonical service hostname shown as the DNS query/answer owner from the stale "
        "CNAME/backend it currently reaches; the transport fixtures explain the gateway "
        "failure and refused backend port.\n\n"
        "The checked config is %s. Its corrected end state is byte-for-byte the shipped "
        "file with exactly one change: the hostname inside `endpoint_url` becomes the "
        "canonical service hostname from the DNS evidence. Preserve the scheme, port, "
        "path, timeout and retry lines exactly."
        % (endpoint, endpoint)
    )
    hints = [
        "Checklist: dig and nslookup show the queried service name separately from its "
        "current CNAME target; the service name, not that stale target, is the endpoint.",
        "Checklist: curl -I shows the 502; curl -v and nc -zv print a connection "
        "diagnostic token and corroborate the refused backend port.",
        "Checklist: edit only the endpoint_url hostname in place with nano; keep the "
        "scheme, port, path, timeout and retry lines byte-for-byte unchanged.",
    ]
    return instr, hints, checks


@troubleshoot_template("selinux-avc")
def t_selinux_avc(rng, fs, fixtures, soft):
    """Mandatory-access-control triage: a denied path has the wrong label."""
    svc = tok(rng, "svc", 6)
    cfg = "/etc/%s/%s.conf" % (svc, svc)
    denied_path = "/srv/%s/blocked/%s.dat" % (svc, tok(rng, "payload", 8))
    allowed_path = "/srv/%s/allowed/%s.dat" % (svc, tok(rng, "release", 8))
    fragment = tok(rng, "avc", 6)
    wrong_context = "system_u:object_r:blocked_%s_t:s0" % hexs(rng, 8)
    correct_context = "system_u:object_r:release_%s_t:s0" % hexs(rng, 8)
    source_context = "system_u:system_r:app_t:s0"
    process_pid = rng.randint(1800, 12000)
    serial = rng.randint(1000, 99999)
    reference_dir = "/etc/%s/references" % svc
    # The label is in the filename, not in the brief: ls -l on the directory
    # exposes it alongside real root ownership in the runtime image.
    reference = "%s/%s.conf" % (reference_dir, correct_context)

    broken_lines = [
        "# %s data source (managed by ops)" % svc,
        "service_name = %s" % svc,
        "source_path = %s" % denied_path,
        "retry_count = 3",
    ]
    broken = "\n".join(broken_lines) + "\n"
    fixed = broken.replace(denied_path, allowed_path)
    wfile(fs, cfg, broken, mode=0o666)
    wfile(fs, denied_path, "# blocked payload for %s\n" % svc, mode=0o600)
    wfile(fs, allowed_path, "# approved payload for %s\n" % svc, mode=0o644)
    reference_text = "\n".join([
        "# canonical SELinux reference for %s" % svc,
        "service_name = %s" % svc,
        "selinux_context = %s" % correct_context,
        "allowed_path = %s" % allowed_path,
        "owner = root",
    ]) + "\n"
    wfile(fs, reference, reference_text, mode=0o644)

    put_fixture(fixtures, "getenforce.txt", "Enforcing\n", "selinux-avc")
    denial_lines = []
    for attempt in range(rng.randint(2, 4)):
        denial_lines.append(
            'type=AVC msg=audit(1795500000.%03d:%d): avc: denied { read open } '
            'for pid=%d comm="%s" name="%s" path="%s" dev="sda2" ino=%d '
            'scontext=%s tcontext=%s tclass=file permissive=0'
            % (rng.randint(1, 999), serial + attempt, process_pid + attempt,
               fragment, os.path.basename(denied_path), denied_path,
               rng.randint(10000, 999999), source_context, wrong_context)
        )
    ausearch = "\n".join(denial_lines + [
        'type=SYSCALL msg=audit(1795500000.001:%d): syscall=openat success=no '
        'exe="/usr/local/bin/%s" subj=%s' % (serial, fragment, source_context),
        'type=AVC msg=audit(1795500001.001:%d): avc: granted { read open } '
        'for pid=%d comm="%s" path="%s" dev="sda2" ino=%d '
        'scontext=%s tcontext=%s tclass=file permissive=0'
        % (serial + 10, process_pid, fragment, allowed_path,
           rng.randint(10000, 999999), source_context, correct_context),
    ]) + "\n"
    put_fixture(fixtures, "ausearch_avc.txt", ausearch, "selinux-avc")

    if denied_path == allowed_path or wrong_context == correct_context:
        raise AssertionError("selinux-avc: denied/allowed evidence is not distinct")
    if denied_path not in broken or allowed_path in broken:
        raise AssertionError("selinux-avc: broken config does not point only at the denied path")
    if fixed != broken.replace(denied_path, allowed_path) or fixed == broken:
        raise AssertionError("selinux-avc: repair is not a path-only change")
    if fixtures["getenforce.txt"] != "Enforcing\n":
        raise AssertionError("selinux-avc: SELinux is not enforcing")
    if sum('avc: denied { read open }' in line for line in denial_lines) != len(denial_lines):
        raise AssertionError("selinux-avc: denial lines missing")
    if any(fragment not in line or denied_path not in line for line in denial_lines):
        raise AssertionError("selinux-avc: denial path/fragment missing")
    if wrong_context not in ausearch or correct_context not in ausearch:
        raise AssertionError("selinux-avc: wrong/canonical contexts missing from AVC evidence")
    if allowed_path not in ausearch or correct_context not in os.path.basename(reference):
        raise AssertionError("selinux-avc: reference lacks canonical label")
    if correct_context not in reference_text or allowed_path not in reference_text:
        raise AssertionError("selinux-avc: reference content lacks allowed-source clue")

    checks = [
        hash_check("selinux-avc-1", fragment,
                   "Submit the process comm token repeated on the AVC denial lines "
                   "from `ausearch -m avc`."),
        hash_check("selinux-avc-2", correct_context,
                   "Submit the full canonical SELinux context for the allowed reference, "
                   "as named by the AVC clue and reference label."),
        file_check("selinux-avc-3", cfg, fixed, "selinux-" + svc,
                   "Repair %s in place: replace only `source_path` with the allowed path "
                   "named by the granted AVC/reference evidence." % cfg,
                   initial=broken),
    ]
    instr = (
        "A service is being denied access by mandatory access control. Start with "
        "`getenforce` and `ausearch -m avc`: correlate the repeated denial's process "
        "token, denied path and wrong context with the broken config at %s. A reference "
        "directory at %s holds the canonical label and allowed-source mapping; use "
        "`ls -l` on that directory to inspect its real owner and label-named reference "
        "file, then read the reference with `cat`.\n\n"
        "The checked config is %s. Its corrected end state is byte-for-byte the shipped "
        "file with exactly one change: `source_path` becomes the allowed path named by "
        "the granted AVC/reference evidence. Leave the service name, retry setting, "
        "comment and every other byte unchanged."
        % (cfg, reference_dir, cfg)
    )
    hints = [
        "Checklist: getenforce establishes whether SELinux can deny access; ausearch "
        "-m avc prints the AVC records, including the repeated comm token and path.",
        "Checklist: read the full tcontext fields, not just the path. The granted AVC "
        "and the label-named file listed by ls -l identify the canonical context.",
        "Checklist: compare the denied source_path with the reference's allowed path; "
        "edit only that line in place with nano, then Ctrl+O writes and Ctrl+X exits.",
    ]
    return instr, hints, checks


@troubleshoot_template("port-conflict")
def t_port_conflict(rng, fs, fixtures, soft):
    """Socket triage: another process owns the service's configured port."""
    svc = tok(rng, "portsvc", 6)
    cfg = "/etc/%s/%s.conf" % (svc, svc)
    held_port = rng.randint(40000, 49000)
    free_port = rng.randint(51000, 59999)
    holder = tok(rng, "listener", 8)
    holder_pid = rng.randint(1500, 32000)
    planner_pid = rng.randint(32001, 60000)
    holder_fd = rng.randint(8, 40)
    connection_fragment = tok(rng, "conn", 8)

    broken_lines = [
        "# %s service configuration (managed by ops)" % svc,
        "service_name = %s" % svc,
        "bind_host = 0.0.0.0",
        "bind_port = %d" % held_port,
        "log_level = warn",
    ]
    broken = "\n".join(broken_lines) + "\n"
    fixed_lines = list(broken_lines)
    fixed_lines[3] = "bind_port = %d" % free_port
    fixed = "\n".join(fixed_lines) + "\n"
    wfile(fs, cfg, broken, mode=0o666)

    put_fixture(fixtures, "ps_aux.txt", "\n".join([
        "USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND",
        "root    %5d  0.0  0.1   1000   100 ?        S    10:00   0:00 "
        "%s --bind 127.0.0.2 --port %d" % (holder_pid, holder, held_port),
        "root    %5d  0.0  0.1   1000   100 ?        S    10:00   0:00 "
        "port-planner --service %s --free-port %d" % (planner_pid, svc, free_port),
        "root       411  0.0  0.1   1000   100 ?        S    10:00   0:00 sshd",
    ]) + "\n", "port-conflict")
    put_fixture_merged(fixtures, "ss_tlnp.txt", "\n".join([
        "State   Recv-Q  Send-Q  Local Address:Port   Peer Address:Port  Process",
        'LISTEN  0       128     127.0.0.2:%-11d 0.0.0.0:*          users:(("%s",pid=%d,fd=%d))'
        % (held_port, holder, holder_pid, holder_fd),
    ]) + "\n", "port-conflict", merge_ss_fixtures)
    put_fixture_merged(fixtures, "curl_v.txt", "\n".join([
        "# curl -v http://localhost:%d/" % held_port,
        "*   Trying 127.0.0.1:%d..." % held_port,
        "* connect to 127.0.0.1 port %d failed: Connection refused" % held_port,
        "* connection %s: refused" % connection_fragment,
        "* Closing connection 0",
    ]) + "\n\n", "port-conflict", merge_curl_fixtures)

    if held_port == free_port or fixed == broken:
        raise AssertionError("port-conflict: conflicting/free ports are not distinct")
    if fixed != broken.replace("bind_port = %d" % held_port,
                               "bind_port = %d" % free_port):
        raise AssertionError("port-conflict: repair is not a bind_port-only change")
    ps_text = fixtures["ps_aux.txt"]
    ss_text = fixtures["ss_tlnp.txt"]
    curl_text = fixtures["curl_v.txt"]
    if holder not in ps_text or str(holder_pid) not in ps_text:
        raise AssertionError("port-conflict: holder process/PID missing from ps")
    if holder not in ss_text or str(held_port) not in ss_text:
        raise AssertionError("port-conflict: holder/port missing from ss")
    if str(free_port) not in ps_text or str(free_port) in ss_text:
        raise AssertionError("port-conflict: proposed free port is not corroborated")
    if "localhost:%d" % held_port not in curl_text \
            or "Connection refused" not in curl_text \
            or connection_fragment not in curl_text:
        raise AssertionError("port-conflict: localhost curl failure evidence missing")

    checks = [
        hash_check("port-conflict-1", holder,
                   "Submit the listener process name from the matching `ps aux | grep` "
                   "row, not its PID."),
        hash_check("port-conflict-2", str(held_port),
                   "Submit the numeric port held by that process in `ss -tlnp`.",
                   plain=str(held_port)),
        file_check("port-conflict-3", cfg, fixed, "port-" + svc,
                   "Repair %s in place: change only `bind_port` to the service's proposed "
                   "free port from the process-table evidence." % cfg,
                   initial=broken),
    ]
    instr = (
        "A service cannot bind its configured port. Use `ss -tlnp` to identify the "
        "listening address, port and process, then `ps aux | grep <candidate>` to "
        "corroborate the holder's PID and command. Inspect the full `ps aux` table "
        "for the port planner's proposed free port, and use "
        "`curl -v localhost:<port>` to check the local endpoint.\n\n"
        "The checked config is %s. Its corrected end state is byte-for-byte the shipped "
        "file with exactly one change: `bind_port` becomes the proposed free port. "
        "Leave `bind_host`, the service name, log level, comment and every other byte "
        "unchanged, and edit the file in place." % cfg
    )
    hints = [
        "Checklist: ss -tlnp lists LISTEN sockets and process owners; correlate the "
        "configured port with the holder, then ps aux | grep <candidate> confirms it.",
        "Checklist: curl -v localhost:<port> shows the local connection result and a "
        "diagnostic token; it corroborates the endpoint but does not choose the repair.",
        "Checklist: the port-planner row in ps aux names a proposed free port for the "
        "service; confirm it is absent from ss, then change only bind_port with nano.",
    ]
    return instr, hints, checks


@troubleshoot_template("oom-kill")
def t_oom_kill(rng, fs, fixtures, soft):
    """Memory-exhaustion triage: correlate an OOM kill with a safe cgroup cap."""
    svc = tok(rng, "memsvc", 6)
    unit = svc + ".service"
    cfg = "/etc/%s/%s.conf" % (svc, svc)
    process = tok(rng, "worker", 8)
    incident = tok(rng, "oom", 8)
    killed_pid = rng.randint(1800, 60000)
    supervisor_pid = rng.randint(700, 1799)
    total = rng.choice([8192, 16384])
    free = rng.randint(32, 128)
    cache = rng.randint(128, 384)
    used = total - free - cache
    available = free + cache // 2
    high_rss = used - rng.randint(96, 320)
    safe_limit = rng.randint(256, 768)
    unsafe_limit = rng.choice([4096, 8192, 16384])
    missing_limit = rng.choice([False, True])

    broken_lines = [
        "# %s service configuration (managed by ops)" % svc,
        "service_name = %s" % svc,
    ]
    if not missing_limit:
        broken_lines.append("memory_limit_mb = %d" % unsafe_limit)
    broken_lines.extend([
        "log_level = warn",
        "restart_delay_seconds = 5",
    ])
    fixed_lines = list(broken_lines)
    limit_line = "memory_limit_mb = %d" % safe_limit
    if missing_limit:
        fixed_lines.insert(2, limit_line)
    else:
        fixed_lines[2] = limit_line
    broken = "\n".join(broken_lines) + "\n"
    fixed = "\n".join(fixed_lines) + "\n"
    wfile(fs, cfg, broken, mode=0o666)

    journal = "\n".join([
        "# journalctl -u %s -n 100 --no-pager" % unit,
        "-- unit %s --" % unit,
        "%s kernel: Out of memory: Killed process %d (%s) total-vm:%d kB, "
        "anon-rss:%d kB, file-rss:%d kB, shmem-rss:0 kB, UID:1000"
        % (ts(rng), killed_pid, process, high_rss * 1024 + 65536,
           high_rss * 1024, rng.randint(0, 12000)),
        "%s kernel: oom_reaper: reaped process %d (%s), now anon-rss:0kB"
        % (ts(rng), killed_pid, process),
        "%s memory-policy[%d]: incident %s approved safe memory_limit_mb=%d "
        "for %s after workload review" % (ts(rng), supervisor_pid, incident,
                                          safe_limit, unit),
        "%s systemd[1]: %s: Main process exited, code=killed, status=9/KILL"
        % (ts(rng), unit),
        "%s systemd[1]: %s: Failed with result 'signal'." % (ts(rng), unit),
    ]) + "\n"
    put_fixture_merged(fixtures, "journalctl_unit.txt", journal,
                       "oom-kill", merge_labelled_reports)
    put_fixture_merged(fixtures, "journalctl_xe.txt",
                       journal.replace("# journalctl -u %s -n 100 --no-pager" % unit,
                                       "# journalctl -xe", 1),
                       "oom-kill", merge_labelled_reports)

    put_fixture_merged(fixtures, "free_m.txt", "# snapshot oom-kill\n" + "\n".join([
        "               total        used        free      shared  buff/cache   available",
        "Mem:        %8d    %8d    %8d    %8d    %8d    %8d"
        % (total, used, free, rng.randint(16, 96), cache, available),
        "Swap:       %8d    %8d    %8d" % (2048, 0, 2048),
    ]) + "\n", "oom-kill", merge_reports)

    mem_percent = 100.0 * high_rss / total
    put_fixture_merged(fixtures, "top.txt", "# snapshot oom-kill\n" + "\n".join([
        "top - %02d:%02d:%02d up 3 days,  7:12,  1 user,  load average: 1.84, 1.62, 1.41"
        % (rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)),
        "Tasks: 121 total,   2 running, 119 sleeping,   0 stopped,   0 zombie",
        "%Cpu(s): 12.4 us,  3.8 sy,  0.0 ni, 81.7 id,  1.2 wa,  0.0 hi,  0.9 si,  0.0 st",
        "MiB Mem :  %8.1f total, %8.1f free, %8.1f used, %8.1f buff/cache"
        % (total, free, used, cache),
        "MiB Swap:   2048.0 total,  2048.0 free,     0.0 used.  %8.1f avail Mem" % available,
        "",
        "    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND",
        "  %5d trainee   20   0 %7d %6d %5d R  88.2 %5.1f   9:41.23 %s"
        % (killed_pid, high_rss * 1024 + 262144, high_rss, cache // 2,
           mem_percent, process),
        "    411 root      20   0   12000   4200   1800 S   0.3   0.1   0:03.11 sshd",
    ]) + "\n", "oom-kill", merge_reports)

    if safe_limit >= unsafe_limit or fixed == broken:
        raise AssertionError("oom-kill: safe/broken memory limits are not distinct")
    if missing_limit:
        if "memory_limit_mb" in broken or fixed_lines[2] != limit_line:
            raise AssertionError("oom-kill: missing-limit repair has the wrong insertion point")
    elif fixed != broken.replace("memory_limit_mb = %d" % unsafe_limit, limit_line):
        raise AssertionError("oom-kill: repair is not a memory-limit-only change")
    if ("memory_limit_mb = %d" % safe_limit) in broken:
        raise AssertionError("oom-kill: broken config already contains the safe limit")
    if not 0 < available < total or not available < high_rss <= used:
        raise AssertionError("oom-kill: memory figures do not show exhaustion")
    oom_line = "Out of memory: Killed process %d (%s)" % (killed_pid, process)
    if journal.count(oom_line) != 1:
        raise AssertionError("oom-kill: killed PID/process line is missing or ambiguous")
    if journal.count("safe memory_limit_mb=%d" % safe_limit) != 1:
        raise AssertionError("oom-kill: safe-limit clue is missing or ambiguous")
    if process not in fixtures["top.txt"] or str(high_rss) not in fixtures["top.txt"]:
        raise AssertionError("oom-kill: top does not corroborate the high-memory process")
    if ("Mem:" in fixtures["free_m.txt"]
            and str(available) not in fixtures["free_m.txt"]):
        raise AssertionError("oom-kill: available memory missing from free output")

    killed_answer = "%d:%s" % (killed_pid, process)
    checks = [
        hash_check("oom-kill-1", incident,
                   "Submit the killed PID and process name from the OOM line in "
                   "`journalctl -u <unit>`, joined by a colon (PID first).",
                   plain=killed_answer),
        hash_check("oom-kill-2", "available-%d" % available,
                   "Submit the numeric available-memory figure in MiB from the Mem row "
                   "of `free -m`.", plain=str(available)),
        file_check("oom-kill-3", cfg, fixed, "memory-" + svc,
                   "Repair %s in place: set only `memory_limit_mb` to the safe limit "
                   "approved in the journal. If the setting is absent, insert it immediately "
                   "after `service_name`; otherwise replace only its value." % cfg,
                   initial=broken),
    ]
    instr = (
        "The service %s was killed after memory exhaustion. Use `journalctl -u %s -n 100 "
        "--no-pager` and `journalctl -xe` to identify the killed PID/process and the incident's "
        "approved safe memory limit. Use `free -m` to read the Mem row's available figure and "
        "`top` to corroborate the high-MEM process.\n\n"
        "The checked config is %s. Its corrected end state is byte-for-byte the shipped file "
        "with only `memory_limit_mb` changed to the approved safe limit. If that setting is "
        "absent, insert it immediately after `service_name`; if present, replace only its value. "
        "Preserve every other line and edit the file in place."
        % (unit, unit, cfg)
    )
    hints = [
        "Checklist: journalctl -u <unit> -n 100 --no-pager and journalctl -xe show the "
        "kernel's OOM kill line; copy the PID and parenthesized process name exactly.",
        "Checklist: free -m reports memory in MiB; use the available column on the Mem row. "
        "top orders processes by resource use and corroborates the memory-heavy process.",
        "Checklist: find the memory-policy incident line for the approved limit, then edit "
        "only memory_limit_mb in nano. Ctrl+O writes in place and Ctrl+X exits.",
    ]
    return instr, hints, checks


@troubleshoot_template("image-pull")
def t_image_pull(rng, fs, fixtures, soft):
    """Container-image triage: replace an unavailable tag with the registry's release tag."""
    name = tok(rng, "pullsvc", 6)
    digest = hexs(rng, 12)
    major, minor, patch = rng.randint(2, 6), rng.randint(0, 9), rng.randint(0, 9)
    correct_tag = "%d.%d.%d" % (major, minor, patch)
    wrong_tag = correct_tag + "-rc1"
    image_name = "registry.example/%s" % name
    correct_image = "%s:%s" % (image_name, correct_tag)
    wrong_image = "%s:%s" % (image_name, wrong_tag)
    reason_fragment = tok(rng, "pull", 8)
    deploy = "/home/trainee/containers/%s.env" % name

    broken_lines = [
        "# container deployment image (managed by ops)",
        "IMAGE=%s" % wrong_image,
        "REPLICAS=2",
        "RESTART_POLICY=always",
    ]
    fixed_lines = list(broken_lines)
    fixed_lines[1] = "IMAGE=%s" % correct_image
    broken = "\n".join(broken_lines) + "\n"
    fixed = "\n".join(fixed_lines) + "\n"
    wfile(fs, deploy, broken, mode=0o644)

    created = "%d minutes ago" % rng.randint(2, 20)
    docker_ps = "\n".join([
        "CONTAINER ID   IMAGE                                 COMMAND                  "
        "CREATED          STATUS                      PORTS     NAMES",
        "%s   %-36s  \"/usr/local/bin/entry…\"   %-15s  %-26s            %s"
        % (digest, wrong_image, created, "Created", name),
    ]) + "\n"
    podman_ps = "\n".join([
        "CONTAINER ID  IMAGE                                 COMMAND               "
        "CREATED         STATUS                      PORTS       NAMES",
        "%s  %-36s  /usr/local/bin/entry  %-14s  %-26s              %s"
        % (digest, wrong_image, created, "Created", name),
    ]) + "\n"
    put_fixture_merged(fixtures, "docker_ps_a.txt", docker_ps,
                       "image-pull", merge_container_ps_fixtures)
    put_fixture_merged(fixtures, "podman_ps_a.txt", podman_ps,
                       "image-pull", merge_container_ps_fixtures)

    docker_logs = "# container %s (image-pull)\n%s" % (name, "\n".join([
        "%s dockerd: Failed to pull image %s: manifest unknown"
        % (ts(rng), wrong_image),
        "%s dockerd: Back-off pulling image %s (ImagePullBackOff); request-id=%s"
        % (ts(rng), wrong_image, reason_fragment),
        "%s registry.example: tag %s is unavailable; available release image is %s"
        % (ts(rng), wrong_tag, correct_image),
    ]) + "\n")
    podman_logs = "# container %s (image-pull)\n%s" % (name, "\n".join([
        "%s Error: %s: manifest unknown" % (ts(rng), wrong_image),
        "%s Back-off pulling image %s (ImagePullBackOff); request-id=%s"
        % (ts(rng), wrong_image, reason_fragment),
        "%s registry.example: tag %s is unavailable; available release image is %s"
        % (ts(rng), wrong_tag, correct_image),
    ]) + "\n")
    put_fixture_merged(fixtures, "docker_logs.txt", docker_logs,
                       "image-pull", merge_reports)
    put_fixture_merged(fixtures, "podman_logs.txt", podman_logs,
                       "image-pull", merge_reports)

    inspect_docker = json.dumps([{
        "Id": digest,
        "Name": "/" + name,
        "Config": {"Image": wrong_image, "Env": ["REPLICAS=2"]},
        "State": {"Status": "created", "Running": False, "ExitCode": 0,
                  "Error": "manifest unknown; ImagePullBackOff; request-id=" + reason_fragment},
        "Labels": {"registry-available-tag": correct_tag},
    }], separators=(",", ":")) + "\n"
    inspect_podman = json.dumps([{
        "Id": digest,
        "Name": name,
        "Image": wrong_image,
        "Config": {"Env": ["REPLICAS=2"]},
        "State": {"Status": "created", "Running": False, "ExitCode": 0,
                  "Error": "manifest unknown; ImagePullBackOff; request-id=" + reason_fragment},
        "Labels": {"registry-available-tag": correct_tag},
    }], separators=(",", ":")) + "\n"
    put_fixture_merged(fixtures, "docker_inspect.txt", inspect_docker,
                       "image-pull", merge_inspect_fixtures)
    put_fixture_merged(fixtures, "podman_inspect.txt", inspect_podman,
                       "image-pull", merge_inspect_fixtures)
    put_soft(soft, fixtures, "kubectl_get_pods.txt", "\n".join([
        "NAME                          READY   STATUS             RESTARTS   AGE",
        "%-30s 0/1     ImagePullBackOff   0          6m" % name,
    ]) + "\n", "image-pull")

    if wrong_tag == correct_tag or fixed == broken:
        raise AssertionError("image-pull: wrong/correct tags are not distinct")
    if fixed != broken.replace("IMAGE=" + wrong_image, "IMAGE=" + correct_image):
        raise AssertionError("image-pull: repair is not an IMAGE-only change")
    if ("IMAGE=%s\n" % correct_image) in broken or ("IMAGE=%s\n" % wrong_image) not in broken:
        raise AssertionError("image-pull: broken deploy file already solved or lacks wrong tag")
    for engine in ("docker", "podman"):
        ps_text = fixtures[engine + "_ps_a.txt"]
        logs_text = fixtures[engine + "_logs.txt"]
        inspect = json.loads(fixtures[engine + "_inspect.txt"])
        own = [obj for obj in inspect
               if obj.get("Name", "").lstrip("/") == name]
        if name not in ps_text or wrong_image not in ps_text or "Created" not in ps_text:
            raise AssertionError("image-pull: %s ps fixture lacks failed container" % engine)
        if (wrong_image not in logs_text or correct_tag not in logs_text
                or reason_fragment not in logs_text
                or "manifest unknown" not in logs_text or "ImagePullBackOff" not in logs_text):
            raise AssertionError("image-pull: %s logs lack failure/release clues" % engine)
        if len(own) != 1 or reason_fragment not in own[0]["State"]["Error"]:
            raise AssertionError("image-pull: %s inspect lacks matching failure" % engine)
        if own[0]["Labels"]["registry-available-tag"] != correct_tag:
            raise AssertionError("image-pull: %s inspect lacks correct release tag" % engine)

    checks = [
        hash_check("image-pull-1", correct_tag,
                   "Submit the full image name and available release tag shown by the "
                   "registry clue in `docker logs` / `podman logs`, as name:tag.",
                   plain=correct_image),
        hash_check("image-pull-2", reason_fragment,
                   "Submit the request-id fragment attached to the manifest/pull-back-off "
                   "failure in the container logs."),
        file_check("image-pull-3", deploy, fixed, "deploy-" + name,
                   "Repair %s in place: replace only the `IMAGE` value with the available "
                   "release image named by the logs and inspect evidence." % deploy,
                   initial=broken),
    ]
    instr = (
        "A container was created but its image could not be pulled. Triage both supported "
        "container tools: `docker ps -a` / `podman ps -a` show the stopped container and "
        "attempted image, `docker logs <name>` / `podman logs <name>` show the pull failure, "
        "request-id and available release tag, and `docker inspect <name>` / "
        "`podman inspect <name>` confirm the configured image and failure.\n\n"
        "The deploy file to repair is %s. Its corrected end state is byte-for-byte the shipped "
        "file with exactly one change: the `IMAGE` value becomes the available release image "
        "named by the evidence. Preserve the comment, replica count, restart policy and every "
        "other byte, and edit the file in place." % deploy
    )
    hints = [
        "Checklist: docker ps -a / podman ps -a lists stopped containers too; match NAMES "
        "to IMAGE and note that Created does not mean the image pull succeeded.",
        "Checklist: docker logs <name> / podman logs <name> distinguishes the unavailable "
        "tag from the registry's available release tag and prints the failure request-id.",
        "Checklist: docker inspect <name> / podman inspect <name> corroborates Config.Image "
        "and State.Error; edit only IMAGE in nano, then Ctrl+O writes and Ctrl+X exits.",
    ]
    return instr, hints, checks


# ---------------------------------------------------------------------------
# output assembly
# ---------------------------------------------------------------------------

def build_stage(tid, seed, fsroot, fixtures=None, soft=None):
    t_rng_seed = int.from_bytes(
        hashlib.sha256(("%s|%s" % (seed, tid)).encode("utf-8")).digest()[:8], "big"
    )
    rng = random.Random(t_rng_seed)
    if fixtures is not None and tid in TROUBLESHOOT_TEMPLATES:
        instr, hints, checks = TROUBLESHOOT_TEMPLATES[tid](rng, fsroot, fixtures, soft)
    else:
        instr, hints, checks = TEMPLATES[tid](rng, fsroot)
    frags = [c["fragment"] for c in checks]
    flag = "FLAG{" + sha_text("|".join(frags))[:12] + "}"
    return {
        "id": tid,
        "instruction": with_submit(instr, checks),
        "hints": hints,
        "checks": checks,
        "flag": flag,
    }


def render_checks(checks):
    blocks = []
    for c in checks:
        lines = ["id=" + c["id"], "type=" + c["type"]]
        if c["type"] == "hash":
            lines.append("answer=" + c["answer"])
        else:
            lines.append("path=" + c["path"])
            lines.append("sha256=" + c["sha256"])
        lines.append("fragment=" + c["fragment"])
        lines.append("hint=" + c["hint"])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _dump_check(c):
    """Test-only view of a check: plaintext answer plus verification metadata.

    For hash checks the plaintext answer is the discovered token.  For file checks it
    is the full EXPECTED CORRECTED file content (never shipped in fs/), and ``sha256``
    is the hash the verifier must see after the trainee repairs the file.
    """
    row = {"id": c["id"], "type": c["type"], "answer": c["plain"], "fragment": c["fragment"]}
    if c["type"] == "file":
        row["path"] = c["path"]
        row["sha256"] = c["sha256"]
    return row


def write_outputs(seed, count, out, dump_path, profile="terminal", pick=None):
    fsroot = os.path.join(out, "fs")
    os.makedirs(fsroot, exist_ok=True)
    stagesdir = os.path.join(out, "stages")
    os.makedirs(stagesdir, exist_ok=True)

    fixtures = None
    if profile == "troubleshoot":
        if pick:
            # Explicit order wins; --set is ignored and becomes len(pick).
            order = list(pick)
            count = len(order)
        else:
            sel = random.Random(seed)
            order = sel.sample(TROUBLESHOOT_IDS, count)
            sel.shuffle(order)
        fixtures = {}
        soft = {}
        stages = [build_stage(tid, seed, fsroot, fixtures, soft) for tid in order]

        fixturesdir = os.path.join(out, "fixtures")
        os.makedirs(fixturesdir, exist_ok=True)
        for name in FIXTURE_NAMES:
            content = fixtures.get(name)
            if content is None:
                content = soft.get(name, NEUTRAL_FIXTURE)
            with open(os.path.join(fixturesdir, name), "w",
                      encoding="utf-8", newline="\n") as fh:
                fh.write(content)
    else:
        # Terminal profile: the original selection and stage construction,
        # byte-for-byte, with no fixtures directory.
        sel = random.Random(seed)
        order = sel.sample(ALL_IDS, count)
        sel.shuffle(order)
        stages = [build_stage(tid, seed, fsroot) for tid in order]

    manifest = ["SEED %d" % seed, "STAGES %d" % len(stages)]
    manifest += ["STAGE " + s["id"] for s in stages]
    with open(os.path.join(out, "manifest"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(manifest) + "\n")

    for idx, stage in enumerate(stages, 1):
        d = os.path.join(stagesdir, "%02d" % idx)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "instruction"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(stage["instruction"])
        with open(os.path.join(d, "hint"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(stage["hints"]) + "\n")
        with open(os.path.join(d, "checks.tsv"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render_checks(stage["checks"]))
        with open(os.path.join(d, "flag.sha256"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(sha_text(stage["flag"]) + "\n")

    final_flag = stages[-1]["flag"]
    with open(os.path.join(out, "final.sha256"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(sha_text(final_flag) + "\n")

    if dump_path:
        dump = {
            "seed": seed,
            "set": count,
            "order": order,
            "stages": {
                s["id"]: {
                    "index": i + 1,
                    "checks": [
                        _dump_check(c)
                        for c in s["checks"]
                    ],
                    "flag": s["flag"],
                }
                for i, s in enumerate(stages)
            },
            "flags": {s["id"]: s["flag"] for s in stages},
            "final_flag": final_flag,
        }
        parent = os.path.dirname(os.path.abspath(dump_path))
        os.makedirs(parent, exist_ok=True)
        with open(dump_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(dump, fh, indent=2, sort_keys=True)
            fh.write("\n")

    return stages, order, final_flag


# ---------------------------------------------------------------------------
# self-test: re-derive every answer from the generated artefacts
# ---------------------------------------------------------------------------

def self_test(seeds, verbose=True):
    """Generate a full 16-stage terminal set *and* the 14-stage troubleshoot set
    per seed, then cross-foot every answer against the artefacts on disk.

    The templates assert their own load-bearing invariants while generating (e.g.
    ``top-talker`` re-reads its log and computes the busiest client).  This routine
    adds the independent, generator-wide checks: flag formula vs ``checks.tsv``
    order, file checks not shipped pre-solved, no answer dump under ``fs/``, the
    two drill-specific claims (top talker, config-drift baseline token), and for
    troubleshoot: the full 32-fixture contract, broken initial files, and
    scenario-specific clue placement across all fourteen drills.
    """
    import re
    import tempfile

    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            print("SELFTEST FAIL: %s" % msg, file=sys.stderr)

    top_re = re.compile(r"(/var/log/sre/access-[^\s]+\.log)")
    drift_re = re.compile(r"(/home/trainee/drift-[^\s]+)/app\.conf")

    def snapshot(text, label):
        marker = "# snapshot %s\n" % label
        if marker not in text:
            return ""
        return text.split(marker, 1)[1].split("\n\n# snapshot ", 1)[0]

    for seed in seeds:
        with tempfile.TemporaryDirectory(prefix="sre-selftest.") as tmp:
            fsroot = os.path.join(tmp, "fs")
            dump_path = os.path.join(tmp, "answers.json")
            stages, order, final_flag = write_outputs(seed, len(ALL_IDS), tmp, dump_path)

            with open(dump_path, "r", encoding="utf-8") as fh:
                dump = json.load(fh)

            check(len(order) == len(ALL_IDS) == len(set(order)),
                  "seed %d: stage selection is not a full unique set" % seed)
            check(dump["order"] == order, "seed %d: dump order differs from build order" % seed)
            check(dump["final_flag"] == final_flag == stages[-1]["flag"],
                  "seed %d: final flag is not the last stage flag" % seed)
            check(dump["set"] == len(ALL_IDS), "seed %d: dump set size" % seed)

            with open(os.path.join(tmp, "manifest"), "r", encoding="utf-8") as fh:
                manifest = fh.read().splitlines()
            check(manifest[0] == "SEED %d" % seed, "seed %d: manifest seed" % seed)
            check(manifest[1] == "STAGES %d" % len(stages), "seed %d: manifest stage count" % seed)
            check(manifest[2:] == ["STAGE " + s["id"] for s in stages],
                  "seed %d: manifest stage order" % seed)

            for idx, stage in enumerate(stages, 1):
                sd = os.path.join(tmp, "stages", "%02d" % idx)
                with open(os.path.join(sd, "instruction"), "r", encoding="utf-8") as fh:
                    instr = fh.read()
                with open(os.path.join(sd, "hint"), "r", encoding="utf-8") as fh:
                    hint = fh.read()
                with open(os.path.join(sd, "flag.sha256"), "r", encoding="utf-8") as fh:
                    flag_digest = fh.read().strip()

                check(hint.strip() != "", "seed %d %s: empty hint file" % (seed, stage["id"]))
                check("%%" not in instr and "%%" not in hint,
                      "seed %d %s: literal double percent in stage prose" % (seed, stage["id"]))
                check(flag_digest == sha_text(stage["flag"]),
                      "seed %d %s: flag.sha256 does not match the stage flag" % (seed, stage["id"]))
                check(stage["flag"].startswith("FLAG{") and len(stage["flag"]) == 18,
                      "seed %d %s: malformed flag" % (seed, stage["id"]))

                for c in stage["checks"]:
                    check(c["id"] in instr, "seed %d: instruction omits check id %s" % (seed, c["id"]))

                # checks.tsv: fragment order defines the flag; file checks must be broken.
                with open(os.path.join(sd, "checks.tsv"), "r", encoding="utf-8") as fh:
                    blocks = [b for b in fh.read().split("\n\n") if b.strip()]
                check(len(blocks) == len(stage["checks"]),
                      "seed %d %s: checks.tsv block count" % (seed, stage["id"]))
                frags = []
                for block in blocks:
                    kv = {}
                    for line in block.splitlines():
                        k, _, v = line.partition("=")
                        kv[k] = v
                    frags.append(kv.get("fragment", ""))
                    if kv.get("type") == "file":
                        shipped = os.path.join(fsroot, kv["path"].lstrip("/"))
                        with open(shipped, "rb") as fh:
                            data = fh.read()
                        check(sha_bytes(data) != kv["sha256"],
                              "seed %d %s: %s ships already solved" % (seed, stage["id"], kv["id"]))
                    elif kv.get("type") == "hash":
                        check(re.fullmatch(r"[0-9a-f]{64}", kv.get("answer", "")) is not None,
                              "seed %d %s: %s answer is not a sha256 digest"
                              % (seed, stage["id"], kv["id"]))
                check(("FLAG{" + sha_text("|".join(frags))[:12] + "}") == stage["flag"],
                      "seed %d %s: flag formula/order mismatch" % (seed, stage["id"]))

                # top-talker: re-derive the winner and its count from the log on disk.
                if stage["id"] == "top-talker":
                    m = top_re.search(instr)
                    check(m is not None, "seed %d: top-talker log path not in instruction" % seed)
                    if m:
                        with open(os.path.join(fsroot, m.group(1).lstrip("/")),
                                  "r", encoding="utf-8") as fh:
                            counted = {}
                            for line in fh:
                                if line.strip():
                                    k = line.split()[0]
                                    counted[k] = counted.get(k, 0) + 1
                        winner = max(counted.items(), key=lambda kv: kv[1])
                        answers = [c["answer"] for c in dump["stages"]["top-talker"]["checks"]]
                        check(winner[0] == answers[0],
                              "seed %d: top-talker answer %s but log winner is %s"
                              % (seed, answers[0], winner[0]))
                        check(str(winner[1]) == answers[1],
                              "seed %d: top-talker count %s but log says %d"
                              % (seed, answers[1], winner[1]))
                        ordered = sorted(counted.values(), reverse=True)
                        check(len(ordered) > 1 and ordered[1] < ordered[0],
                              "seed %d: top-talker has no clear winner" % seed)

                # config-drift: the baseline token lives only in the backup.
                if stage["id"] == "config-drift":
                    m = drift_re.search(instr)
                    check(m is not None, "seed %d: config-drift paths not in instruction" % seed)
                    if m:
                        base = os.path.join(fsroot, m.group(1).lstrip("/"))
                        with open(os.path.join(base, "app.conf"), "r", encoding="utf-8") as fh:
                            live = fh.read()
                        with open(os.path.join(base, "app.conf.bak"), "r", encoding="utf-8") as fh:
                            backup = fh.read()
                        token = dump["stages"]["config-drift"]["checks"][1]["answer"]
                        check(token in backup, "seed %d: config-drift token missing from backup" % seed)
                        check(token not in live, "seed %d: config-drift token also in app.conf" % seed)

            for dirpath, _dirnames, filenames in os.walk(fsroot):
                check("answers.json" not in filenames,
                      "seed %d: answers.json leaked into %s" % (seed, dirpath))

            # ---- troubleshoot profile: all fourteen templates, all 32 fixtures ----
            with tempfile.TemporaryDirectory(prefix="sre-selftest-ts.") as ts_tmp:
                ts_dump_path = os.path.join(ts_tmp, "answers.json")
                ts_stages, ts_order, ts_final = write_outputs(
                    seed, len(TROUBLESHOOT_IDS), ts_tmp, ts_dump_path,
                    profile="troubleshoot", pick=list(TROUBLESHOOT_IDS))
                with open(ts_dump_path, "r", encoding="utf-8") as fh:
                    ts_dump = json.load(fh)
                ts_fs = os.path.join(ts_tmp, "fs")
                ts_fixtures = os.path.join(ts_tmp, "fixtures")

                present = sorted(os.listdir(ts_fixtures)) if os.path.isdir(ts_fixtures) else []
                missing = [n for n in FIXTURE_NAMES if n not in present]
                extra = [n for n in present if n not in FIXTURE_NAMES]
                check(present == sorted(FIXTURE_NAMES),
                      "seed %d: troubleshoot fixtures set mismatch (missing=%s extra=%s)"
                      % (seed, missing, extra))
                for name in FIXTURE_NAMES:
                    if name in present:
                        with open(os.path.join(ts_fixtures, name), encoding="utf-8") as fh:
                            check("%%" not in fh.read(),
                                  "seed %d: literal double percent in fixture %s" % (seed, name))
                check(ts_order == list(TROUBLESHOOT_IDS),
                      "seed %d: troubleshoot pick order mismatch" % seed)
                check(ts_dump["order"] == list(TROUBLESHOOT_IDS),
                      "seed %d: troubleshoot dump order mismatch" % seed)
                check(ts_dump["set"] == len(TROUBLESHOOT_IDS),
                      "seed %d: troubleshoot dump set size" % seed)
                check(ts_final == ts_stages[-1]["flag"],
                      "seed %d: troubleshoot final flag is not the last stage flag" % seed)
                with open(os.path.join(ts_tmp, "manifest"), "r", encoding="utf-8") as fh:
                    ts_manifest = fh.read().splitlines()
                check(ts_manifest == ["SEED %d" % seed, "STAGES %d" % len(TROUBLESHOOT_IDS)]
                      + ["STAGE " + tid for tid in TROUBLESHOOT_IDS],
                      "seed %d: troubleshoot manifest mismatch: %r" % (seed, ts_manifest))

                for idx, stage in enumerate(ts_stages, 1):
                    sid = stage["id"]
                    sd = os.path.join(ts_tmp, "stages", "%02d" % idx)
                    with open(os.path.join(sd, "checks.tsv"), "r", encoding="utf-8") as fh:
                        blocks = [b for b in fh.read().split("\n\n") if b.strip()]
                    check(len(blocks) == 3, "seed %d %s: expected 3 checks, got %d"
                          % (seed, sid, len(blocks)))
                    rows = []
                    for block in blocks:
                        kv = {}
                        for line in block.splitlines():
                            k, _, v = line.partition("=")
                            kv[k] = v
                        rows.append(kv)
                    frags = [r.get("fragment", "") for r in rows]
                    check(("FLAG{" + sha_text("|".join(frags))[:12] + "}") == stage["flag"],
                          "seed %d %s: flag does not recompute from checks.tsv" % (seed, sid))
                    check(ts_dump["stages"][sid]["flag"] == stage["flag"],
                          "seed %d %s: dump flag mismatch" % (seed, sid))
                    dumped = {d["id"]: d for d in ts_dump["stages"][sid]["checks"]}
                    for row in rows:
                        cid = row["id"]
                        check(cid in dumped, "seed %d %s: %s missing from dump" % (seed, sid, cid))
                        row_dump = dumped.get(cid, {})
                        if row.get("type") == "hash":
                            check(re.fullmatch(r"[0-9a-f]{64}", row.get("answer", "")) is not None,
                                  "seed %d %s/%s: answer is not a sha256" % (seed, sid, cid))
                            check(sha_text(row_dump.get("answer", "")) == row.get("answer"),
                                  "seed %d %s/%s: dump answer does not hash to checks.tsv"
                                  % (seed, sid, cid))
                        elif row.get("type") == "file":
                            shipped = os.path.join(ts_fs, row["path"].lstrip("/"))
                            with open(shipped, "rb") as fh:
                                data = fh.read()
                            check(sha_bytes(data) != row["sha256"],
                                  "seed %d %s/%s: file ships already solved"
                                  % (seed, sid, cid))
                            check(row_dump.get("sha256") == row["sha256"],
                                  "seed %d %s/%s: dump sha256 mismatch" % (seed, sid, cid))
                            check(sha_text(row_dump.get("answer", "")) == row["sha256"],
                                  "seed %d %s/%s: repaired bytes do not hash to sha256"
                                  % (seed, sid, cid))
                        else:
                            check(False, "seed %d %s/%s: unknown check type" % (seed, sid, cid))
                    with open(os.path.join(sd, "instruction"), "r", encoding="utf-8") as fh:
                        ts_instr = fh.read()
                    check("%%" not in ts_instr and all("%%" not in h for h in stage["hints"]),
                          "seed %d %s: literal double percent in troubleshoot prose"
                          % (seed, sid))
                    for c in stage["checks"]:
                        check(c["id"] in ts_instr,
                              "seed %d %s: instruction omits %s" % (seed, sid, c["id"]))

                # The Kubernetes drills must retain their own clues after composition.
                if "k8s-crashloop" in ts_order:
                    with open(os.path.join(ts_fixtures, "kubectl_get_pods.txt"),
                              encoding="utf-8") as fh:
                        k8s_pods = fh.read()
                    with open(os.path.join(ts_fixtures, "kubectl_describe_pod.txt"),
                              encoding="utf-8") as fh:
                        k8s_describe = fh.read()
                    with open(os.path.join(ts_fixtures, "kubectl_logs.txt"),
                              encoding="utf-8") as fh:
                        k8s_logs = fh.read()
                    with open(os.path.join(ts_fixtures, "kubectl_get_events.txt"),
                              encoding="utf-8") as fh:
                        k8s_events = fh.read()
                    with open(os.path.join(ts_fixtures, "kubectl_get_deploy.txt"),
                              encoding="utf-8") as fh:
                        k8s_deploy = fh.read()
                    with open(os.path.join(ts_fixtures, "kubectl_get_configmap.txt"),
                              encoding="utf-8") as fh:
                        k8s_configmap = fh.read()
                    k8s_checks = ts_dump["stages"]["k8s-crashloop"]["checks"]
                    k8s_answers = [c["answer"] for c in k8s_checks]
                    k8s_pod, k8s_reason = k8s_answers[0].split(":", 1)
                    k8s_actual_match = re.search(r"^\s+key: (\S+)$", k8s_answers[2],
                                                 re.MULTILINE)
                    k8s_actual_key = k8s_actual_match.group(1) if k8s_actual_match else ""
                    check(k8s_pod in k8s_pods and k8s_pod in k8s_describe
                          and k8s_pod in k8s_events
                          and os.path.basename(k8s_checks[2]["path"])[:-5] in k8s_deploy,
                          "seed %d: shared Kubernetes tables lost k8s-crashloop evidence" % seed)
                    check(k8s_reason in k8s_logs and k8s_answers[1] in k8s_logs
                          and k8s_actual_key in k8s_configmap,
                          "seed %d: shared Kubernetes logs/configmap lost k8s-crashloop clues"
                          % seed)
                if "svc-selector" in ts_order:
                    with open(os.path.join(ts_fixtures, "kubectl_get_svc.txt"),
                              encoding="utf-8") as fh:
                        selector_svc = fh.read()
                    with open(os.path.join(ts_fixtures, "kubectl_get_endpoints.txt"),
                              encoding="utf-8") as fh:
                        selector_endpoints = fh.read()
                    with open(os.path.join(ts_fixtures, "kubectl_get_events.txt"),
                              encoding="utf-8") as fh:
                        selector_events = fh.read()
                    with open(os.path.join(ts_fixtures, "curl_v.txt"),
                              encoding="utf-8") as fh:
                        selector_curl = fh.read()
                    selector_checks = ts_dump["stages"]["svc-selector"]["checks"]
                    selector_answers = [c["answer"] for c in selector_checks]
                    selector_cfg = os.path.join(ts_fs, selector_checks[2]["path"].lstrip("/"))
                    with open(selector_cfg, encoding="utf-8") as fh:
                        selector_broken = fh.read()
                    service_row = next((line for line in selector_svc.splitlines()
                                        if re.match(r"^" + re.escape(selector_answers[0]) + r"\s",
                                                    line)), "")
                    service_fields = service_row.split()
                    selector_sd = os.path.join(ts_tmp, "stages", "%02d"
                                               % ts_dump["stages"]["svc-selector"]["index"])
                    with open(os.path.join(selector_sd, "instruction"), encoding="utf-8") as fh:
                        selector_instr = fh.read()
                    check(bool(service_fields) and "ClusterIP" in service_fields
                          and ("app=" + selector_answers[0]) in service_row,
                          "seed %d: Service name/selector missing from kubectl get svc" % seed)
                    check(selector_answers[1] in selector_svc
                          and selector_answers[1] in selector_endpoints
                          and selector_answers[1] in selector_events
                          and "<none>" in selector_endpoints
                          and "EndpointsNotFound" in selector_events,
                          "seed %d: selector-mismatch/no-endpoint evidence is incomplete" % seed)
                    check(len(service_fields) >= 5 and service_fields[2] in selector_curl
                          and service_fields[4].split("/", 1)[0] in selector_curl
                          and "Connection refused" in selector_curl,
                          "seed %d: Service curl refusal lacks cluster address/port" % seed)
                    check(selector_answers[2] == selector_broken.replace(
                              "      labels:\n",
                              "      labels:\n        app: %s\n" % selector_answers[0])
                          and selector_answers[2] != selector_broken,
                          "seed %d: Service selector repair is not the exact added pod label" % seed)
                    check(all(command in selector_instr for command in
                              ("kubectl get svc", "kubectl get endpoints", "kubectl get events",
                               "kubectl describe pod", "kubectl get deploy", "curl -v")),
                          "seed %d: Service selector instruction omits its checklist" % seed)
                    check(selector_answers[0] not in selector_instr
                          and selector_answers[1] not in selector_instr,
                          "seed %d: Service selector instruction reveals an answer" % seed)
                if "resource-limits" in ts_order:
                    with open(os.path.join(ts_fixtures, "kubectl_get_pods.txt"),
                              encoding="utf-8") as fh:
                        limits_pods = fh.read()
                    with open(os.path.join(ts_fixtures, "kubectl_describe_pod.txt"),
                              encoding="utf-8") as fh:
                        limits_describe = fh.read()
                    with open(os.path.join(ts_fixtures, "kubectl_get_events.txt"),
                              encoding="utf-8") as fh:
                        limits_events = fh.read()
                    with open(os.path.join(ts_fixtures, "kubectl_logs.txt"),
                              encoding="utf-8") as fh:
                        limits_logs = fh.read()
                    limits_checks = ts_dump["stages"]["resource-limits"]["checks"]
                    limits_answers = [c["answer"] for c in limits_checks]
                    limits_cfg = os.path.join(ts_fs, limits_checks[2]["path"].lstrip("/"))
                    with open(limits_cfg, encoding="utf-8") as fh:
                        limits_broken = fh.read()
                    limit_match = re.search(r"^              memory: (\S+)$",
                                            limits_answers[2], re.MULTILINE)
                    limit_value = limit_match.group(1) if limit_match else ""
                    limits_sd = os.path.join(ts_tmp, "stages", "%02d"
                                             % ts_dump["stages"]["resource-limits"]["index"])
                    with open(os.path.join(limits_sd, "instruction"), encoding="utf-8") as fh:
                        limits_instr = fh.read()
                    pod_row = next((line for line in limits_pods.splitlines()
                                    if re.match(r"^" + re.escape(limits_answers[0]) + r"\s",
                                                line)), "")
                    check(bool(pod_row) and "CrashLoopBackOff" in pod_row
                          and limits_answers[0] in limits_describe
                          and "QoS Class:        BestEffort" in limits_describe
                          and "Limits:        <none>" in limits_describe
                          and "OOMKilled" in limits_describe,
                          "seed %d: resource-limit pod/QoS/OOM evidence is incomplete" % seed)
                    check(limits_answers[1] in limits_events
                          and limits_answers[1] in limits_describe
                          and limits_answers[1] in limits_logs and "OOMKilling" in limits_events,
                          "seed %d: resource-limit OOM fragment is missing or inconsistent" % seed)
                    check(bool(limit_value) and limit_value in limits_events
                          and limit_value in limits_describe and limit_value in limits_logs,
                          "seed %d: reviewed memory limit is not revealed by fixtures" % seed)
                    expected_limit_lines = limits_broken.splitlines()
                    image_index = expected_limit_lines.index(
                        "          image: registry.example/batch-worker:3.4.1")
                    expected_limit_lines[image_index + 1:image_index + 1] = [
                        "          resources:", "            limits:",
                        "              memory: %s" % limit_value,
                    ]
                    check("resources:" not in limits_broken
                          and limits_answers[2] == "\n".join(expected_limit_lines) + "\n",
                          "seed %d: resource-limit repair is not the exact inserted block" % seed)
                    check(all(command in limits_instr for command in
                              ("kubectl get pods", "kubectl describe pod", "kubectl get events",
                               "kubectl logs", "kubectl get deploy")),
                          "seed %d: resource-limit instruction omits its checklist" % seed)
                    check(limits_answers[0] not in limits_instr
                          and limits_answers[1] not in limits_instr
                          and (not limit_value or limit_value not in limits_instr),
                          "seed %d: resource-limit instruction reveals an answer" % seed)

                # The new drills must surface their clues in the real fixtures.
                if "disk-full" in ts_order:
                    with open(os.path.join(ts_fixtures, "df_h.txt"), encoding="utf-8") as fh:
                        df = fh.read()
                    with open(os.path.join(ts_fixtures, "du_sh.txt"), encoding="utf-8") as fh:
                        du = fh.read()
                    ans = [c["answer"] for c in ts_dump["stages"]["disk-full"]["checks"]]
                    check(df.count("100%") == 1,
                          "seed %d: df_h must show exactly one 100%% filesystem" % seed)
                    check(ans[0] in df, "seed %d: disk-full mount answer not in df_h" % seed)
                    check(ans[1] in du, "seed %d: disk-full dir answer not in du_sh" % seed)
                if "container-crash" in ts_order:
                    with open(os.path.join(ts_fixtures, "docker_ps_a.txt"),
                              encoding="utf-8") as fh:
                        ps = fh.read()
                    with open(os.path.join(ts_fixtures, "docker_logs.txt"),
                              encoding="utf-8") as fh:
                        logs = fh.read()
                    ans = [c["answer"] for c in ts_dump["stages"]["container-crash"]["checks"]]
                    cname, _, cexit = ans[0].rpartition(":")
                    check(cname and cname in ps and ("Exited (%s)" % cexit) in ps,
                          "seed %d: container name/exit not in docker ps" % seed)
                    check(ans[1] in logs, "seed %d: missing var not in logs" % seed)
                if "nginx-config" in ts_order:
                    with open(os.path.join(ts_fixtures, "nginx_t.txt"),
                              encoding="utf-8") as fh:
                        nginx_test = fh.read()
                    nginx_checks = ts_dump["stages"]["nginx-config"]["checks"]
                    ans = [c["answer"] for c in nginx_checks]
                    nginx_cfg = os.path.join(ts_fs, nginx_checks[2]["path"].lstrip("/"))
                    with open(nginx_cfg, "rb") as fh:
                        initial_bytes = fh.read()
                    initial = initial_bytes.decode("utf-8")
                    fixed = ans[2]
                    changed = [(a, b) for a, b in zip(initial.splitlines(), fixed.splitlines())
                               if a != b]
                    check(ans[0] in nginx_test and nginx_checks[2]["path"] in nginx_test,
                          "seed %d: nginx error fragment/path missing from nginx -t" % seed)
                    check(len(initial.splitlines()) == len(fixed.splitlines())
                          and len(changed) == 2
                          and sum(a + ";" == b for a, b in changed) == 1
                          and sum(a.strip().startswith("server_name ")
                                  and b.strip().startswith("server_name ")
                                  for a, b in changed) == 1,
                          "seed %d: nginx repair is not exactly semicolon + server_name" % seed)
                    nginx_sd = os.path.join(ts_tmp, "stages", "%02d"
                                            % ts_dump["stages"]["nginx-config"]["index"])
                    with open(os.path.join(nginx_sd, "instruction"), encoding="utf-8") as fh:
                        nginx_instr = fh.read()
                    ref_match = re.search(r"(/etc/nginx/references/[^\s`]+\.conf)", nginx_instr)
                    check(ref_match is not None,
                          "seed %d: nginx instruction lacks reference path" % seed)
                    if ref_match:
                        with open(os.path.join(ts_fs, ref_match.group(1).lstrip("/")),
                                  encoding="utf-8") as fh:
                            reference = fh.read()
                        server_line = next((line.strip() for line in fixed.splitlines()
                                            if line.strip().startswith("server_name ")), "")
                        check(server_line in reference,
                              "seed %d: nginx reference lacks corrected server_name" % seed)
                    check(ans[1] == "nginx",
                          "seed %d: nginx owner answer is not the service user" % seed)
                    with open(os.path.join(ts_fixtures, "getenforce.txt"),
                              encoding="utf-8") as fh:
                        nginx_selinux_mode = fh.read()
                    with open(os.path.join(ts_fixtures, "ausearch_avc.txt"),
                              encoding="utf-8") as fh:
                        nginx_avc = fh.read()
                    if "selinux-avc" in ts_order:
                        check(nginx_selinux_mode == "Enforcing\n"
                              and "avc: denied" in nginx_avc,
                              "seed %d: shared SELinux fixtures must carry the selected AVC denial" % seed)
                    else:
                        check(nginx_selinux_mode in ("Permissive\n", "Disabled\n")
                              and nginx_avc != NEUTRAL_FIXTURE,
                              "seed %d: nginx-only SELinux context must be benign" % seed)
                if "dns-mismatch" in ts_order:
                    with open(os.path.join(ts_fixtures, "dig.txt"), encoding="utf-8") as fh:
                        dig = fh.read()
                    with open(os.path.join(ts_fixtures, "nslookup.txt"), encoding="utf-8") as fh:
                        nslookup = fh.read()
                    with open(os.path.join(ts_fixtures, "curl_v.txt"), encoding="utf-8") as fh:
                        curl_v = fh.read()
                    with open(os.path.join(ts_fixtures, "nc_zv.txt"), encoding="utf-8") as fh:
                        nc = fh.read()
                    dns_checks = ts_dump["stages"]["dns-mismatch"]["checks"]
                    ans = [c["answer"] for c in dns_checks]
                    dns_cfg = os.path.join(ts_fs, dns_checks[2]["path"].lstrip("/"))
                    with open(dns_cfg, encoding="utf-8") as fh:
                        broken_endpoint = fh.read()
                    wrong_host = re.search(r"endpoint_url=http://([^:]+):", broken_endpoint)
                    check(wrong_host is not None and wrong_host.group(1) != ans[0]
                          and ans[0] not in broken_endpoint
                          and ans[2] == broken_endpoint.replace(wrong_host.group(1), ans[0]),
                          "seed %d: DNS endpoint repair is not hostname-only" % seed)
                    check(ans[0] in dig and ans[0] in nslookup,
                          "seed %d: canonical DNS hostname missing from answers" % seed)
                    if wrong_host:
                        check(wrong_host.group(1) in dig and wrong_host.group(1) in nslookup,
                              "seed %d: stale DNS target missing from answers" % seed)
                    check(ans[1] in curl_v and ans[1] in nc and "Connection refused" in nc,
                          "seed %d: DNS transport fragment/refusal missing" % seed)
                if "selinux-avc" in ts_order:
                    with open(os.path.join(ts_fixtures, "getenforce.txt"),
                              encoding="utf-8") as fh:
                        selinux_mode = fh.read()
                    with open(os.path.join(ts_fixtures, "ausearch_avc.txt"),
                              encoding="utf-8") as fh:
                        avc = fh.read()
                    selinux_checks = ts_dump["stages"]["selinux-avc"]["checks"]
                    selinux_ans = [c["answer"] for c in selinux_checks]
                    selinux_cfg = os.path.join(ts_fs, selinux_checks[2]["path"].lstrip("/"))
                    with open(selinux_cfg, encoding="utf-8") as fh:
                        selinux_broken = fh.read()
                    source_match = re.search(r"source_path = ([^\n]+)", selinux_broken)
                    denied_path = source_match.group(1) if source_match else ""
                    denials = [line for line in avc.splitlines() if "avc: denied" in line]
                    wrong_contexts = re.findall(r"tcontext=([^\s]+)", "\n".join(denials))
                    grant_line = next((line for line in avc.splitlines()
                                       if "avc: granted" in line), "")
                    grant_match = re.search(r'path="([^"]+)"', grant_line)
                    allowed_path = grant_match.group(1) if grant_match else ""
                    selinux_sd = os.path.join(ts_tmp, "stages", "%02d"
                                              % ts_dump["stages"]["selinux-avc"]["index"])
                    with open(os.path.join(selinux_sd, "instruction"), encoding="utf-8") as fh:
                        selinux_instr = fh.read()
                    ref_match = re.search(r"(/etc/[^/\s`]+/references)", selinux_instr)
                    check(selinux_mode == "Enforcing\n" and len(denials) >= 2,
                          "seed %d: SELinux enforcement/denial evidence missing" % seed)
                    check(bool(denied_path) and denied_path in avc
                          and all(selinux_ans[0] in line and denied_path in line
                                  for line in denials),
                          "seed %d: AVC denial token/path not discoverable" % seed)
                    check(bool(wrong_contexts) and all(c != selinux_ans[1]
                                                       for c in wrong_contexts)
                          and selinux_ans[1] in avc,
                          "seed %d: wrong/canonical SELinux contexts not distinct in AVC" % seed)
                    check(bool(allowed_path) and allowed_path != denied_path
                          and selinux_ans[2] == selinux_broken.replace(denied_path, allowed_path),
                          "seed %d: SELinux repair is not a denied-to-allowed path change" % seed)
                    check(ref_match is not None,
                          "seed %d: SELinux instruction lacks reference directory" % seed)
                    if ref_match:
                        ref_dir = os.path.join(ts_fs, ref_match.group(1).lstrip("/"))
                        refs = os.listdir(ref_dir) if os.path.isdir(ref_dir) else []
                        check(any(name == selinux_ans[1] + ".conf" for name in refs),
                              "seed %d: real reference filename lacks canonical context" % seed)
                        matching = [name for name in refs if name == selinux_ans[1] + ".conf"]
                        if matching:
                            with open(os.path.join(ref_dir, matching[0]),
                                      encoding="utf-8") as fh:
                                reference = fh.read()
                            check(selinux_ans[1] in reference and "owner = root" in reference,
                                  "seed %d: SELinux reference lacks context/owner clue" % seed)
                    check(all(command in selinux_instr for command in
                              ("getenforce", "ausearch -m avc", "ls -l")),
                          "seed %d: SELinux instruction omits its command checklist" % seed)
                    check(selinux_ans[0] not in selinux_instr
                          and selinux_ans[1] not in selinux_instr,
                          "seed %d: SELinux instruction reveals a hash answer" % seed)
                if "port-conflict" in ts_order:
                    with open(os.path.join(ts_fixtures, "ps_aux.txt"),
                              encoding="utf-8") as fh:
                        port_ps = fh.read()
                    with open(os.path.join(ts_fixtures, "ss_tlnp.txt"),
                              encoding="utf-8") as fh:
                        port_ss = fh.read()
                    with open(os.path.join(ts_fixtures, "curl_v.txt"),
                              encoding="utf-8") as fh:
                        port_curl = fh.read()
                    port_checks = ts_dump["stages"]["port-conflict"]["checks"]
                    port_ans = [c["answer"] for c in port_checks]
                    port_cfg = os.path.join(ts_fs, port_checks[2]["path"].lstrip("/"))
                    with open(port_cfg, encoding="utf-8") as fh:
                        port_broken = fh.read()
                    held_match = re.search(r"bind_port = (\d+)", port_broken)
                    free_match = re.search(r"bind_port = (\d+)", port_ans[2])
                    held_port = held_match.group(1) if held_match else ""
                    free_port = free_match.group(1) if free_match else ""
                    port_sd = os.path.join(ts_tmp, "stages", "%02d"
                                           % ts_dump["stages"]["port-conflict"]["index"])
                    with open(os.path.join(port_sd, "instruction"), encoding="utf-8") as fh:
                        port_instr = fh.read()
                    check(port_ans[0] in port_ps and port_ans[1] in port_ss
                          and held_port == port_ans[1],
                          "seed %d: port holder/port missing from process evidence" % seed)
                    check(bool(free_port) and free_port != held_port
                          and free_port in port_ps and free_port not in port_ss
                          and port_ans[2] == port_broken.replace(
                              "bind_port = " + held_port, "bind_port = " + free_port),
                          "seed %d: port repair/free-port evidence inconsistent" % seed)
                    check("localhost:" + held_port in port_curl
                          and "Connection refused" in port_curl,
                          "seed %d: localhost curl failure missing for conflicting port" % seed)
                    check(all(command in port_instr for command in
                              ("ps aux | grep", "ss -tlnp", "curl -v localhost:")),
                          "seed %d: port instruction omits its command checklist" % seed)
                    check(port_ans[0] not in port_instr and held_port not in port_instr
                          and (not free_port or free_port not in port_instr),
                          "seed %d: port instruction reveals a hash/file answer" % seed)
                if "oom-kill" in ts_order:
                    with open(os.path.join(ts_fixtures, "journalctl_unit.txt"),
                              encoding="utf-8") as fh:
                        oom_journal = fh.read()
                    with open(os.path.join(ts_fixtures, "free_m.txt"),
                              encoding="utf-8") as fh:
                        oom_free = snapshot(fh.read(), "oom-kill")
                    with open(os.path.join(ts_fixtures, "top.txt"),
                              encoding="utf-8") as fh:
                        oom_top = snapshot(fh.read(), "oom-kill")
                    oom_checks = ts_dump["stages"]["oom-kill"]["checks"]
                    oom_ans = [c["answer"] for c in oom_checks]
                    killed_pid, process = oom_ans[0].split(":", 1)
                    oom_match = re.search(
                        r"Out of memory: Killed process (\d+) \(([^)]+)\)", oom_journal)
                    safe_match = re.search(r"safe memory_limit_mb=(\d+)", oom_journal)
                    avail_match = re.search(r"^Mem:.*\s(\d+)$", oom_free, re.MULTILINE)
                    process_row = next((line for line in oom_top.splitlines()
                                        if process in line), "")
                    row_fields = process_row.split()
                    oom_cfg = os.path.join(ts_fs, oom_checks[2]["path"].lstrip("/"))
                    with open(oom_cfg, encoding="utf-8") as fh:
                        oom_broken = fh.read()
                    safe_line = ("memory_limit_mb = " + safe_match.group(1)
                                 if safe_match else "")
                    if "memory_limit_mb = " in oom_broken:
                        expected_oom = oom_broken.replace(
                            next(line for line in oom_broken.splitlines()
                                 if line.startswith("memory_limit_mb = ")), safe_line)
                    else:
                        expected_lines = oom_broken.splitlines()
                        service_index = next(i for i, line in enumerate(expected_lines)
                                             if line.startswith("service_name = "))
                        expected_lines.insert(service_index + 1, safe_line)
                        expected_oom = "\n".join(expected_lines) + "\n"
                    oom_sd = os.path.join(ts_tmp, "stages", "%02d"
                                          % ts_dump["stages"]["oom-kill"]["index"])
                    with open(os.path.join(oom_sd, "instruction"), encoding="utf-8") as fh:
                        oom_instr = fh.read()
                    check(oom_match is not None
                          and oom_match.groups() == (killed_pid, process)
                          and oom_journal.count("Out of memory: Killed process") == 1,
                          "seed %d: OOM killed PID/process line is missing or ambiguous" % seed)
                    check(safe_match is not None and avail_match is not None
                          and avail_match.group(1) == oom_ans[1],
                          "seed %d: OOM safe-limit/free-available clues are inconsistent" % seed)
                    check(len(row_fields) >= 12 and row_fields[0] == killed_pid
                          and row_fields[-1] == process
                          and int(row_fields[5]) > int(oom_ans[1]),
                          "seed %d: OOM top evidence does not corroborate high memory" % seed)
                    check(oom_ans[2] == expected_oom and oom_ans[2] != oom_broken,
                          "seed %d: OOM repair is not the exact memory-limit change" % seed)
                    check(all(command in oom_instr for command in
                              ("journalctl -u", "journalctl -xe", "free -m", "top")),
                          "seed %d: OOM instruction omits its command checklist" % seed)
                    check(oom_ans[0] not in oom_instr and process not in oom_instr,
                          "seed %d: OOM instruction reveals a hash answer" % seed)
                if "image-pull" in ts_order:
                    image_checks = ts_dump["stages"]["image-pull"]["checks"]
                    image_ans = [c["answer"] for c in image_checks]
                    image_cfg = os.path.join(ts_fs, image_checks[2]["path"].lstrip("/"))
                    with open(image_cfg, encoding="utf-8") as fh:
                        image_broken = fh.read()
                    wrong_match = re.search(r"^IMAGE=(.+)$", image_broken, re.MULTILINE)
                    wrong_image = wrong_match.group(1) if wrong_match else ""
                    container_name = os.path.basename(image_checks[2]["path"])[:-4]
                    check(bool(wrong_image) and wrong_image != image_ans[0]
                          and image_ans[2] == image_broken.replace(
                              "IMAGE=" + wrong_image, "IMAGE=" + image_ans[0]),
                          "seed %d: image-pull repair is not an IMAGE-only change" % seed)
                    for engine in ("docker", "podman"):
                        with open(os.path.join(ts_fixtures, engine + "_ps_a.txt"),
                                  encoding="utf-8") as fh:
                            image_ps = fh.read()
                        with open(os.path.join(ts_fixtures, engine + "_logs.txt"),
                                  encoding="utf-8") as fh:
                            image_logs = fh.read()
                        with open(os.path.join(ts_fixtures, engine + "_inspect.txt"),
                                  encoding="utf-8") as fh:
                            image_inspect = json.load(fh)
                        own = next((obj for obj in image_inspect
                                    if obj.get("Name", "").lstrip("/") == container_name), {})
                        check(container_name in image_ps and wrong_image in image_ps
                              and "Created" in image_ps,
                              "seed %d: %s image-pull ps evidence missing" % (seed, engine))
                        check(wrong_image in image_logs and image_ans[0] in image_logs
                              and image_ans[1] in image_logs
                              and "manifest unknown" in image_logs
                              and "ImagePullBackOff" in image_logs,
                              "seed %d: %s image-pull log clues missing" % (seed, engine))
                        check(own.get("Labels", {}).get("registry-available-tag")
                              == image_ans[0].rsplit(":", 1)[-1]
                              and image_ans[1] in own.get("State", {}).get("Error", ""),
                              "seed %d: %s image-pull inspect clues missing" % (seed, engine))
                    image_sd = os.path.join(ts_tmp, "stages", "%02d"
                                            % ts_dump["stages"]["image-pull"]["index"])
                    with open(os.path.join(image_sd, "instruction"), encoding="utf-8") as fh:
                        image_instr = fh.read()
                    check(all(command in image_instr for command in
                              ("docker ps -a", "podman ps -a", "docker logs", "podman logs",
                               "docker inspect", "podman inspect")),
                          "seed %d: image-pull instruction omits its command checklist" % seed)
                    check(image_ans[0] not in image_instr and image_ans[1] not in image_instr,
                          "seed %d: image-pull instruction reveals a hash answer" % seed)
                if "tls-expiry" in ts_order:
                    with open(os.path.join(ts_fixtures, "curl_v.txt"), encoding="utf-8") as fh:
                        tls_curl = fh.read()
                    with open(os.path.join(ts_fixtures, "curl_I.txt"), encoding="utf-8") as fh:
                        tls_head = fh.read()
                    with open(os.path.join(ts_fixtures, "dig.txt"), encoding="utf-8") as fh:
                        tls_dig = fh.read()
                    with open(os.path.join(ts_fixtures, "nc_zv.txt"), encoding="utf-8") as fh:
                        tls_nc = fh.read()
                    tls_checks = ts_dump["stages"]["tls-expiry"]["checks"]
                    tls_ans = [c["answer"] for c in tls_checks]
                    tls_cfg = os.path.join(ts_fs, tls_checks[2]["path"].lstrip("/"))
                    with open(tls_cfg, encoding="utf-8") as fh:
                        tls_broken = fh.read()
                    tls_parts = tls_ans[0].split(":", 2)
                    tls_cn = tls_parts[0] if len(tls_parts) == 3 else ""
                    tls_date = tls_parts[1] if len(tls_parts) == 3 else ""
                    tls_exp_fragment = tls_parts[2] if len(tls_parts) == 3 else ""
                    old_match = re.search(r"^certificate_file = (.+)$", tls_broken, re.MULTILINE)
                    old_path = old_match.group(1) if old_match else ""
                    tls_sd = os.path.join(ts_tmp, "stages", "%02d"
                                          % ts_dump["stages"]["tls-expiry"]["index"])
                    with open(os.path.join(tls_sd, "instruction"), encoding="utf-8") as fh:
                        tls_instr = fh.read()
                    with open(os.path.join(tls_sd, "hint"), encoding="utf-8") as fh:
                        tls_hint = fh.read()
                    check(bool(tls_cn) and ("subject: CN=" + tls_cn) in tls_curl
                          and ("expire date: " + tls_date) in tls_curl
                          and ("certificate expiry fragment: " + tls_exp_fragment) in tls_curl
                          and "certificate has expired" in tls_curl
                          and "certificate has expired" in tls_head,
                          "seed %d: TLS curl evidence lacks CN/date/fragment or expiry failure" % seed)
                    check(bool(old_path) and old_path != tls_ans[1]
                          and tls_ans[1] in tls_curl
                          and tls_ans[2] == tls_broken.replace(old_path, tls_ans[1]),
                          "seed %d: TLS repair/reference path is inconsistent" % seed)
                    check(tls_cn in tls_dig and " IN A " in tls_dig
                          and tls_cn in tls_nc and "succeeded" in tls_nc,
                          "seed %d: TLS DNS/open-port evidence is incomplete" % seed)
                    check(all(command in tls_instr for command in
                              ("curl -v", "curl -I", "dig", "nc -zv")),
                          "seed %d: TLS instruction omits its command checklist" % seed)
                    check(tls_ans[0] not in tls_instr and tls_ans[1] not in tls_instr
                          and tls_ans[0] not in tls_hint and tls_ans[1] not in tls_hint,
                          "seed %d: TLS instruction/hints reveal an answer" % seed)
                if "dependency-down" in ts_order:
                    with open(os.path.join(ts_fixtures, "systemctl_status.txt"),
                              encoding="utf-8") as fh:
                        dep_status = fh.read()
                    with open(os.path.join(ts_fixtures, "journalctl_unit.txt"),
                              encoding="utf-8") as fh:
                        dep_journal = fh.read()
                    with open(os.path.join(ts_fixtures, "ss_tlnp.txt"), encoding="utf-8") as fh:
                        dep_ss = fh.read()
                    with open(os.path.join(ts_fixtures, "curl_v.txt"), encoding="utf-8") as fh:
                        dep_curl = fh.read()
                    with open(os.path.join(ts_fixtures, "ping.txt"), encoding="utf-8") as fh:
                        dep_ping = fh.read()
                    dep_checks = ts_dump["stages"]["dependency-down"]["checks"]
                    dep_ans = [c["answer"] for c in dep_checks]
                    dep_cfg = os.path.join(ts_fs, dep_checks[2]["path"].lstrip("/"))
                    with open(dep_cfg, encoding="utf-8") as fh:
                        dep_broken = fh.read()
                    dep_unit, _, dep_fragment = dep_ans[0].partition(":")
                    wrong_host_match = re.search(r"^dependency_host = (.+)$", dep_broken,
                                                 re.MULTILINE)
                    wrong_port_match = re.search(r"^dependency_port = (\d+)$", dep_broken,
                                                 re.MULTILINE)
                    correct_host_match = re.search(r"^dependency_host = (.+)$", dep_ans[2],
                                                   re.MULTILINE)
                    correct_port_match = re.search(r"^dependency_port = (\d+)$", dep_ans[2],
                                                   re.MULTILINE)
                    wrong_host = wrong_host_match.group(1) if wrong_host_match else ""
                    wrong_port = wrong_port_match.group(1) if wrong_port_match else ""
                    correct_host = correct_host_match.group(1) if correct_host_match else ""
                    correct_port = correct_port_match.group(1) if correct_port_match else ""
                    dep_sd = os.path.join(ts_tmp, "stages", "%02d"
                                          % ts_dump["stages"]["dependency-down"]["index"])
                    with open(os.path.join(dep_sd, "instruction"), encoding="utf-8") as fh:
                        dep_instr = fh.read()
                    with open(os.path.join(dep_sd, "hint"), encoding="utf-8") as fh:
                        dep_hint = fh.read()
                    check(bool(dep_unit) and "Active: failed" in dep_status
                          and dep_unit in dep_status and dep_fragment in dep_status
                          and dep_unit in dep_journal and dep_fragment in dep_journal,
                          "seed %d: dependency unit/fragment evidence is incomplete" % seed)
                    check("Connection refused" in dep_journal and wrong_host in dep_journal
                          and wrong_port in dep_journal and correct_host in dep_journal
                          and dep_ans[1] in dep_journal,
                          "seed %d: dependency refused/approved endpoint clues are incomplete" % seed)
                    check(bool(correct_port) and correct_port == dep_ans[1]
                          and correct_port != wrong_port and correct_port in dep_ss
                          and wrong_port not in dep_ss,
                          "seed %d: dependency ss listening-port evidence is inconsistent" % seed)
                    check("502 Bad Gateway" in dep_curl and dep_unit in dep_curl
                          and correct_host in dep_ping and "0% packet loss" in dep_ping,
                          "seed %d: dependency curl/ping evidence is incomplete" % seed)
                    expected_dep = dep_broken.replace(
                        "dependency_host = " + wrong_host,
                        "dependency_host = " + correct_host).replace(
                        "dependency_port = " + wrong_port,
                        "dependency_port = " + correct_port)
                    check(dep_ans[2] == expected_dep and dep_ans[2] != dep_broken,
                          "seed %d: dependency repair is not exactly host plus port" % seed)
                    check(all(command in dep_instr for command in
                              ("systemctl status", "journalctl -u", "curl -v", "ss -tlnp")),
                          "seed %d: dependency instruction omits its command checklist" % seed)
                    check(dep_ans[0] not in dep_instr and correct_host not in dep_instr
                          and ("dependency_port = " + dep_ans[1]) not in dep_instr
                          and dep_ans[0] not in dep_hint and correct_host not in dep_hint
                          and ("dependency_port = " + dep_ans[1]) not in dep_hint,
                          "seed %d: dependency instruction/hints reveal an answer" % seed)

                if verbose:
                    print("selftest seed %d: troubleshoot %d stages + %d fixtures checked"
                          % (seed, len(ts_stages), len(present)))

            if verbose:
                print("selftest seed %d: %d stages checked" % (seed, len(stages)))

    if failures:
        print("SELFTEST FAILED: %d assertion(s)" % len(failures), file=sys.stderr)
        return 1
    print("SELFTEST OK: %d seed(s), %d stages each" % (len(seeds), len(ALL_IDS)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate randomized SRE terminal challenges.")
    ap.add_argument("--seed", type=int, default=None, help="deterministic integer seed")
    ap.add_argument("--set", dest="set_size", type=int, default=None,
                    help="number of stages (1..%d)" % len(ALL_IDS))
    ap.add_argument("--out", default=None, help="output directory")
    ap.add_argument("--dump-answers", dest="dump", default=None,
                    help="write test-only plaintext answers JSON here")
    ap.add_argument("--profile", choices=["terminal", "troubleshoot"], default="terminal",
                    help="challenge profile (default: terminal, unchanged output)")
    ap.add_argument("--pick", dest="pick", default=None, metavar="ID1,ID2",
                    help="troubleshoot only: use exactly these template ids, in this order")
    ap.add_argument("--self-test", dest="self_test", metavar="SEEDS", default=None,
                    help="generate a full set for each comma-separated seed and "
                         "cross-check every answer against the artefacts on disk")
    args = ap.parse_args(argv)

    if args.self_test is not None:
        try:
            seeds = [int(s) for s in args.self_test.replace(" ", ",").split(",") if s]
        except ValueError:
            ap.error("--self-test expects comma-separated integers")
        if not seeds:
            ap.error("--self-test expects at least one seed")
        return self_test(seeds)

    if args.seed is None or args.set_size is None or args.out is None:
        ap.error("--seed, --set and --out are required (unless --self-test is used)")

    pick = None
    if args.pick is not None:
        if args.profile != "troubleshoot":
            ap.error("--pick is only valid with --profile troubleshoot")
        pick = [part.strip() for part in args.pick.split(",") if part.strip()]
        if len(pick) != len(set(pick)):
            ap.error("--pick must not contain duplicate template ids")
        unknown = [tid for tid in pick if tid not in TROUBLESHOOT_IDS]
        if unknown:
            ap.error("unknown troubleshoot template id(s): %s (choose from %s)"
                     % (", ".join(unknown), ", ".join(TROUBLESHOOT_IDS)))
        if not pick:
            ap.error("--pick must name at least one template id")

    if args.profile == "troubleshoot":
        if pick is None and not 1 <= args.set_size <= len(TROUBLESHOOT_IDS):
            ap.error("--set must be between 1 and %d without --pick" % len(TROUBLESHOOT_IDS))
    elif not 1 <= args.set_size <= len(ALL_IDS):
        ap.error("--set must be between 1 and %d" % len(ALL_IDS))

    stages, order, final_flag = write_outputs(
        args.seed, args.set_size, args.out, args.dump,
        profile=args.profile, pick=pick,
    )
    print("seed=%d stages=%d order=%s final_flag=%s" % (
        args.seed, len(stages), ",".join(order), final_flag
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
