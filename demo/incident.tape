# demo/incident.tape - one complete Incident mode attempt, recorded for real.
#
# This is a TEMPLATE, not a runnable recording: demo/render-incident.sh picks a
# fixed seed, generates the k8s-crashloop answer key, and substitutes the
# double-underscore tokens into a temporary copy (VHS resolves `Source` and
# `Output` relative to the directory it is started in, so the render script
# runs it from its own scratch directory).  Regenerate with: make demo-incident
#
# Nothing is scripted for effect: every line is a real command typed into a
# real shell / real sshd session against a real throwaway container.  The
# waits are gates on those real sessions, not decoration.
#
# Timing note (same as demo/sample.tape): VHS 0.12.1's `Wait+Screen` only ever
# matches the first screenful of the session buffer, so it cannot gate a
# multi-screen run.  The plain `Wait` form matches the *current line* - the
# shell prompt once a command has finished - so each step uses Type/Enter, a
# short Sleep (so the gate cannot match the prompt from before Enter), then
# Wait on the prompt.

Source style.tape

Output "__OUT__/incident-run.mp4"
Output "__OUT__/incident-run.gif"

Require __ENGINE__
Require make
Require ssh

Set Shell "bash"

# -- hidden setup: plumbing only, never faked output -------------------------
# The throwaway demo instance/state/container/port keep the caller's real
# .instance/, state/ and container untouched.
Hide
Type "PS1='$ '"
Enter
Type "cd __REPO__"
Enter
Type "export __DEMOENV__"
Enter
Show

# -- 1. build the image and start the single incident (real build/container) -
Type "make up MODE=incident PICK=k8s-crashloop SEED=__SEED__ SET=1"
Enter
Sleep 1s
Wait@300s /^\$$/
Sleep 3s

# -- 2. log in as the trainee: real sshd, real password prompt ---------------
Type "make ssh"
Enter
Wait@60s /password:/
Type "__PASSWORD__"
Enter
Sleep 1s
Wait@60s /:~\$$/
Sleep 2s

# -- 3. read the stage brief -------------------------------------------------
Type "verify next"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 4s

# -- 4. two real kubectl diagnostics (shorthand forms, real fixtures) --------
Type "kubectl get po"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 2s

Type "kubectl logs __POD__"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 3s

# -- 5. submit the two hash answers -----------------------------------------
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

# -- 6. make the one-line in-place repair the brief asks for, and prove it ---
Type "sed -i 's/__MISSING__/__ACTUAL__/' __MANIFEST__ && tail -2 __MANIFEST__"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 2s

# The file check hashes the repaired manifest; the path is the argument the
# stage brief itself tells you to submit.
Type "verify check __ID3__ __MANIFEST__"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 2s

# -- 7. earn the stage flag and unlock: the NEXT instruction is printed ------
Type "verify stage"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 3s

Type "verify unlock __FLAG1__"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 4s

# With PICK=k8s-crashloop this is the only stage; `verify next` makes the
# follow-up instruction ("verify finish") explicit on screen.
Type "verify next"
Enter
Sleep 500ms
Wait@60s /:~\$$/
Sleep 2s

# -- 8. leave the session and tear the container down ------------------------
Type "exit"
Enter
Sleep 1s
Wait@120s /^\$$/
Sleep 2s

Type "make down MODE=incident"
Enter
Sleep 1s
Wait@180s /^\$$/
Sleep 3s
