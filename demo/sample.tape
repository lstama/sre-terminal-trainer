# demo/sample.tape - one complete sample attempt, recorded from the real harness.
#
# This file is a TEMPLATE, not a runnable recording: demo/render.sh picks a
# seed, reads the generated answers, and substitutes the __PLACEHOLDER__ tokens
# into a temporary copy (VHS resolves `Source` and `Output` relative to the
# directory it is started in, so render.sh runs it from its own scratch dir).
# Regenerate the video with: make demo
#
# Nothing is scripted for effect: every line is a real command typed into a
# real shell against a real container, and the waits below are gates on real
# sessions, not decoration.
#
# Timing note: VHS 0.12.1's `Wait+Screen` only ever matches the first
# screenful of the session buffer (measured: a marker past ~27 lines never
# matches), so it cannot gate a multi-screen run like this one. The plain
# `Wait` form matches the *current line*, which is exactly the shell prompt
# once a command has finished - that is the gate used throughout, with a
# short lead-in sleep so it cannot match the prompt from before Enter.

Source style.tape

Output "__OUT__/sample-run.mp4"
Output "__OUT__/sample-run.gif"

Require __ENGINE__
Require make
Require ssh

Set Shell "bash"

# -- hidden setup -----------------------------------------------------------
# A short prompt and the throwaway demo instance.  Hidden because it is
# plumbing (the demo must never touch the real .instance/ or state/), not
# because any output is being faked: the commands below still really run.
Hide
Type "PS1='$ '"
Enter
Type "cd __REPO__"
Enter
Type "export __DEMOENV__"
Enter
Show

# -- 1. build the image and start the drills (real build, real container) ---
Type "make up SEED=__SEED__"
Enter
Sleep 1s
Wait@300s /^\$$/
Sleep 3s

# -- 2. log in as the trainee: real sshd, real password prompt --------------
Type "make ssh"
Enter
Wait@60s /password:/
Type "__PASSWORD__"
Enter
Sleep 1s
Wait@60s /:~\$$/
Sleep 2s

# -- 3. read the stage 1 brief ---------------------------------------------
Type "verify next"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 4s

# -- 4. investigate: the grep work the brief actually asks for -------------
Type "grep -n 'rollout failed' __LOG__"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 2s

Type "grep -A2 'rollout failed' __LOG__"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 3s

Type "grep -c 'ERROR deploy' __LOG__"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 2s

# -- 5. submit the answers, earn the stage flag, unlock the next stage ------
Type "verify check __ID1__ __A1__"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 1s

Type "verify check __ID2__ __A2__"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 1s

Type "verify check __ID3__ __A3__"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 2s

Type "verify stage"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 3s

Type "verify unlock __FLAG1__"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 8s

# -- 6. leave the session and tear the container down -----------------------
Type "exit"
Enter
Sleep 1s
Wait@120s /^\$$/
Sleep 2s

Type "make down"
Enter
Sleep 1s
Wait@180s /^\$$/
Sleep 3s
