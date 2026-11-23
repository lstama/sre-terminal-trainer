#!/usr/bin/env python3
"""Deterministic, randomized sysadmin terminal-challenge generator.

Python 3 standard library only.  Everything written by this script lives under
the directory passed with --out (plus the optional --dump-answers file).

CLI:
    python3 generate.py --seed SEED --set N --out DIR [--dump-answers FILE]
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
    frag = tok(rng, "sess")
    topn = rng.randint(380, 520)
    total = 20000
    topip = ip(rng)
    others = [ip(rng) for _ in range(14)]
    pool = []
    for _ in range(topn):
        pool.append((topip, frag))
    for _ in range(total - topn):
        pool.append((rng.choice(others), None))
    rng.shuffle(pool)
    wfile(fs, logp, "\n".join(access_line(rng, c, f) for c, f in pool) + "\n")
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
    sizes = [1200, 2600, 4300, 7800]
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
        "retry_count = 3", "# baseline-ref %s" % frag2, "log_level = warn",
    ]
    fixed = "\n".join(base_lines) + "\n"       # expected corrected bytes, in memory only
    drift_lines = list(base_lines)
    drift_lines[3] = "upstream_host = old.internal.example"
    broken = "\n".join(drift_lines) + "\n"
    wfile(fs, cfg, broken)                      # checked path: ships BROKEN (drifted)
    # The backup keeps the correct host + baseline token but has its own header, so the
    # exact expected bytes are never shipped as any fs file.
    bak_lines = list(base_lines)
    bak_lines[0] = "# application configuration (backup snapshot)"
    wfile(fs, bak, "\n".join(bak_lines) + "\n")
    checks = [
        file_check("config-drift-1", cfg, fixed, frag1,
                   "Restore the drifted upstream_host line in app.conf from the backup.", initial=broken),
        hash_check("config-drift-2", frag2, "Submit the baseline reference token found only in the backup."),
    ]
    instr = (
        "The config %s drifted from the reference backup %s. Run diff on the two files. "
        "The corrected end state for %s is: keep its own `# application configuration` "
        "header and every other line unchanged, and replace only the drifted "
        "upstream_host line with the value shown in the backup (`upstream_host = %s`). "
        "Do not copy the backup's header. Report the baseline reference token kept in "
        "the backup too." % (cfg, bak, cfg, frag1)
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
    specs = [
        ("public.txt", 0o644), ("run.sh", 0o755), ("secrets.env", 0o600),
        ("group.cfg", 0o640), ("world.txt", 0o777), ("readonly.key", 0o400),
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
# output assembly
# ---------------------------------------------------------------------------

def build_stage(tid, seed, fsroot):
    t_rng_seed = int.from_bytes(
        hashlib.sha256(("%s|%s" % (seed, tid)).encode("utf-8")).digest()[:8], "big"
    )
    rng = random.Random(t_rng_seed)
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


def write_outputs(seed, count, out, dump_path):
    fsroot = os.path.join(out, "fs")
    os.makedirs(fsroot, exist_ok=True)
    stagesdir = os.path.join(out, "stages")
    os.makedirs(stagesdir, exist_ok=True)

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


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate randomized SRE terminal challenges.")
    ap.add_argument("--seed", type=int, required=True, help="deterministic integer seed")
    ap.add_argument("--set", dest="set_size", type=int, required=True,
                    help="number of stages (1..%d)" % len(ALL_IDS))
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--dump-answers", dest="dump", default=None,
                    help="write test-only plaintext answers JSON here")
    args = ap.parse_args(argv)

    if not 1 <= args.set_size <= len(ALL_IDS):
        ap.error("--set must be between 1 and %d" % len(ALL_IDS))

    stages, order, final_flag = write_outputs(args.seed, args.set_size, args.out, args.dump)
    print("seed=%d stages=%d order=%s final_flag=%s" % (
        args.seed, len(stages), ",".join(order), final_flag
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
