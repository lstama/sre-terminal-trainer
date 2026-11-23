# Walkthrough — a full session, spoiler-free

This is the guided tour that `README.md` links to. It explains **how a session works** and
**what each drill is training**, without giving away a single answer. Every challenge is
generated from a seed, so the paths, tokens and counts you see will differ from any run
described elsewhere; use the brief `verify next` prints as the source of truth.

---

## 1. The shape of a session

```
host                                    container (one build = one challenge set)
----                                    -----------------------------------------
make up            ── builds image ──▶  8 stages, seeded, files already in place
make ssh           ── ssh 127.0.0.1 ─▶  bash as uid 1000, no sudo
                                        verify next / hint / check / stage / unlock
                                        verify finish <final-flag>
make down          ◀─ records ───────   state/records.jsonl is copied out
make best                               your runs, fastest first
```

* `make up` prints the seed, the port, the login and the stage list. It publishes the port
  on loopback only, so nothing outside your machine can reach it.
* `make ssh` drops you into `/home/trainee` over a real SSH session with a password prompt.
* The container is disposable. If you ruin a file, `make down && make up` with the same
  seed rebuilds exactly the same challenge set. `make new` picks a fresh seed instead.

## 2. The `verify` loop

Work one stage at a time. Nothing later is visible until you unlock it.

| Step | Command | What it gives you |
|---|---|---|
| read the brief | `verify next` | stage title, instruction, the ids of its checks, and how to submit each one |
| check your position | `verify status` | current stage, elapsed time, how many checks are done |
| get unstuck | `verify hint` | this stage's hints, one screenful at a time; `verify hint 2` jumps ahead |
| answer a check | `verify check <check-id> <answer>` | `OK (id): <fragment>` or `NOT OK` — answers are never printed |
| collect the stage flag | `verify stage` | the flag for this stage, once every check in it passed |
| open the next stage | `verify unlock <flag>` | the next brief; the last unlock says "everything is unlocked" |
| record the run | `verify finish <final-flag>` | writes one record; a second `finish` is refused |
| start over | `verify reset` | clears progress *and* the finished marker; files and records are untouched |

Two things surprise people:

1. **Hints are cumulative, not a tier system.** Each stage ships three short hints, and
   every check adds its own line at the end. `verify hint` prints the first one; keep
   asking (`verify hint 3`, `verify hint 5`, …) until you have what you need. The number of
   hints varies per stage.
2. **A file check hashes the file, not your argument.** For those checks the instruction
   names a path and describes the exact corrected end state; the argument you type is
   ignored. Repair the file, then run `verify check <id> anything`. "Exact" means exact:
   same lines, same order, same line endings, same trailing newline. If a check keeps
   failing after an edit you believe is right, diff your file against what the brief says
   should be there, byte for byte.

Files under `/home/trainee` are yours and editable. Logs under `/var/log/sre` are inputs:
read-only on purpose, so the evidence stays intact while you work.

## 3. What the drills train

The generator picks a subset (8 by default, up to all 16) and orders it. Any of the
following can show up, in any order, at any position. Each entry says what the drill is
for, the tools it expects, and where the hints live — not what the answer is.

### Log triage

**`deploy-error`** — read a long deploy log and pull three facts out of it: two identifiers
around the failure and a count. Skills: `grep` with context (`-A`, `-B`), `grep -c`, not
being fooled by near-miss lines. Hints: `grep -rniE`, `grep -B3 -A3`, `grep -c`.

**`noise-filter`** — a log dominated by repetitive health-check chatter with a small number
of real failures buried in it. Skills: inverting a match with `grep -v`, chaining filters,
counting only the survivors. Hints: `grep -v`, piping two greps, `grep -c`.

**`log-growth`** — several service logs of different sizes; one is the runaway. Skills:
comparing file sizes (`ls -lah`, `du -ah | sort -h`) instead of opening everything, then
reading a line count with `wc -l` and previewing with `head`. Hints: `ls`/`wc`, `du`,
`head`.

**`live-tail`** — a log whose interesting markers sit at the very end. Skills: following a
stream (`tail -f`, `less +F`) and stopping it cleanly with Ctrl+C, plus `tail -n` when
following is overkill. Hints: `tail -f`, `less +F`, `tail -n 2`.

**`copy-mode`** — the same problem at much larger scale: a 100,000-line rolling log with two
markers near the bottom. Skills: tmux copy mode (`Ctrl+b [` then `End`), or grep when
scrolling is the wrong tool. Hints: copy mode, `tail -n`, targeted `grep`.

**`window-fragments`** — three log segments in three directories, one marker each. Skills:
juggling several files in tmux windows (`Ctrl+b c`, `Ctrl+b n`/`p`) instead of losing your
place, and suppressing noise with `grep -v`. Hints: tmux window keys, `grep -v`,
`grep -c` to confirm exactly one match.

### Search and navigation

**`conf-hunt`** — one rogue config file hidden in a deep tree of decoy configs. Skills:
`find` by name, `tree` for the shape of a directory, recursive content search for the keys
you need, `cd -`/`pwd` to keep your bearings. Hints: `find -name`, `tree -L 2`, `grep -rn`.

**`nav-maze`** — a deliberately deep directory chain with decoy branches. Skills: reading a
tree before walking it, navigating with absolute vs relative paths, and `cd -` when you need
to backtrack. Hints: `tree -L 2`, `cd -`/`pwd`, `ls -lah`.

### Editing and file repair

These are the drills where the corrected end state matters more than the command you use.
Read the brief twice: it says exactly which lines change and that everything else must stay
byte-identical.

**`crlf-fix`** — a config saved with carriage returns that breaks parsers. Skills: seeing
invisible characters (`cat -A` shows `^M$` vs `$`), confirming with `file`, and normalising
line endings — either in `nano`'s search-and-replace or with a standard tool. Hints:
`cat -A`, `file`/`stat`, nano's replace.

**`line-surgery`** — a 20,000-line config with one corrupt line and a specified replacement.
Skills: `grep -n` to find the line number, then jumping straight to it (`nano` Ctrl+_,
`sed -n '<n>p'` to confirm) rather than scrolling. Hints: `grep -n`, nano's go-to-line,
`sed -n`.

**`nano-replace`** — the same wrong hostname repeated throughout a small config. Skills:
counting occurrences *before* editing, then a global replace in nano (Ctrl+\), and saving
with Ctrl+O / Ctrl+X. Hints: `grep -c`, nano replace, save/exit.

**`cut-paste`** — a runbook whose numbered steps have been shuffled. Skills: line surgery in
an editor: cut a line with `Ctrl+k`, paste with `Ctrl+u`, and keep the surrounding numbering
intact. Hints: `grep -n '^STEP'`, cut/paste keys, `sort -n` as a preview.

**`config-drift`** — a live config has drifted from its backup. Skills: `diff`ing two files,
applying only the meaningful difference (not "copy the backup over the top"), and reading a
value that is only kept in the backup. Hints: `diff`, editing in nano, `stat` to compare
ages.

**`split-incident`** — cross-reference a long log while repairing a small config that still
holds a `PENDING` placeholder. Skills: working in two places at once with a tmux split
(`Ctrl+b %`, `Ctrl+b o`), searching in nano (`Ctrl+w`, Alt+W to repeat), and substituting
exactly one line. Hints: `grep -n` + split keys, pane switching + nano search, `grep -A7`.

### Inspect and report

**`perm-audit`** — a directory of files with mixed modes; exactly one is a credential file
left at mode 0600. Skills: reading permission columns (`ls -l`), octal modes (`stat -c %a`),
and selecting by mode with `find -perm`. Hints: `ls -l`, `stat -c`, `find -perm 0600`.

**`top-talker`** — a large access log where one client is far busier than the rest. Skills:
the classic pipeline `awk '{print $1}' | sort | uniq -c | sort -nr | head` — and then
counting that single client's lines with `grep -c`. Hints: the awk/sort/uniq pipeline,
`sort -nr` + `head -n 1`, `grep -c`.

## 4. A sensible order of attack

1. `verify next`, read the brief, note which checks are hash checks and which are file
   checks.
2. For each check, decide the smallest command that answers it — this is the point of the
   exercise; the drills are chosen so the answer is one pipeline away.
3. Submit hash answers as you find them; repair file-check targets last, since editing
   interrupts reading.
4. If a check fails, re-read its line in the brief and the per-check hint at the end of
   `verify hint`; they usually name the exact tool.
5. `verify stage` when the checks are green, `verify unlock <flag>` with the printed flag,
   repeat for the next stage. Keep the stage flags — you need the last one plus the final
   flag to finish.
6. `verify finish <final-flag>`, log out, `make down`, `make best`.

## 5. When you get stuck

* Not sure where you are? `verify status`.
* A check says `NOT OK` for a file you edited: confirm the file content, not your memory of
  it — `diff` it against the end state described in the brief, and check the trailing byte.
* Locked out of the next stage: `verify stage` must succeed *before* `verify unlock`, and
  the flag is case-sensitive text, not a hash.
* Want a clean slate: `make down && make up SEED=<the printed seed>` rebuilds the same
  challenges and wipes progress. `verify reset` only clears progress inside the running
  container.
* `verify --help` always lists the commands.
