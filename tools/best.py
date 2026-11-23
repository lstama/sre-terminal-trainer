#!/usr/bin/env python3
"""Sort sre-terminal-trainer completion records, fastest first.

Records are written by `verify finish` (one JSON object per line) inside the
container and copied to the host by `make down`.
"""
import datetime
import json
import sys


def main(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print("skipping unparseable record on line %d: %s" % (lineno, exc), file=sys.stderr)

    rows.sort(key=lambda r: float(r.get("elapsed_seconds") or r.get("seconds") or 0))

    print("%10s  %7s  %6s  %s" % ("elapsed", "seed", "stages", "finished"))
    for rec in rows:
        elapsed = float(rec.get("elapsed_seconds") or rec.get("seconds") or 0)
        stages = rec.get("stages", [])
        when = datetime.datetime.fromtimestamp(rec.get("epoch", 0)).strftime("%Y-%m-%d %H:%M")
        print("%9ds  %7s  %6d  %s" % (elapsed, rec.get("seed", "?"), len(stages), when))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: best.py <records.jsonl>")
    main(sys.argv[1])
