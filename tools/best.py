#!/usr/bin/env python3
"""Sort sre-terminal-trainer completion records, fastest first.

Records are written by `verify finish` (one JSON object per line) inside the
container and copied to the host by `make down`. A mode argument filters to one
practice mode; without it, all modes are shown together. Records written before
mode tracking (no "mode"/"hints_used" fields) are still listed under "?".
"""
import datetime
import json
import sys


def load(path, mode=None):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                print("skipping unparseable record on line %d: %s" % (lineno, exc),
                      file=sys.stderr)
                continue
            if mode is None or rec.get("mode") == mode:
                rows.append(rec)
    return rows


def main(path, mode=None):
    rows = load(path, mode)
    rows.sort(key=lambda r: float(r.get("elapsed_seconds") or r.get("seconds") or 0))

    print("%10s  %9s  %6s  %7s  %6s  %s"
          % ("elapsed", "mode", "hints", "seed", "stages", "finished"))
    for rec in rows:
        elapsed = float(rec.get("elapsed_seconds") or rec.get("seconds") or 0)
        stages = rec.get("stages", [])
        when = datetime.datetime.fromtimestamp(rec.get("epoch", 0)).strftime("%Y-%m-%d %H:%M")
        print("%9ds  %9s  %6s  %7s  %6d  %s"
              % (elapsed, str(rec.get("mode", "?")), str(rec.get("hints_used", "?")),
                 rec.get("seed", "?"), len(stages), when))


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: best.py <records.jsonl> [terminal|incident]")
    if len(sys.argv) == 3 and sys.argv[2] not in ("terminal", "incident"):
        sys.exit("mode must be terminal or incident")
    main(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
