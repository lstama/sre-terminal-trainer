# Reproducing the demo

The GIF and MP4 committed in this directory are a real, unscripted session against a real container — nothing is typed for effect. This page covers how to re-record them; you only need it to regenerate the assets.

![A full sample attempt: make up, make ssh, verify next, a grep on the deploy log, three verified answers, the stage flag and the next brief](sample-run.gif)

[Watch the full video (MP4)](sample-run.mp4)

## Re-record

```sh
make demo       # rewrite docs/demo/sample-run.mp4 and .gif
make demo-mp4   # only the MP4
make demo-gif   # only the GIF
```

Requires [VHS](https://github.com/charmbracelet/vhs) and `ffmpeg` (which VHS uses), plus `gifsicle` if installed to shrink the GIF. The recordings are committed, so none of this is needed just to run the trainer.

## How a recording is made

[`demo/render.sh`](../../demo/render.sh) drives everything against a throwaway instance so your own `.instance/`, `state/` and container are never touched:

- Regenerates the answer key on the host for a fixed `SEED` (default `8`) and asserts stage 1 is `deploy-error`, a short grep drill; `SEED=…` selects another seed.
- Warms the image cache first, then records a real `make up`.
- Uses `.demo-instance/`, `.demo-state/`, container `sre-trainer-demo` and port `2299`, removing that instance whether the recording succeeds or fails.
- Fills [`demo/sample.tape`](../../demo/sample.tape) with the generated log path, check ids, answers and stage flag, then runs VHS from a scratch directory.

[`demo/style.tape`](../../demo/style.tape) holds every presentation setting (theme, font size, window bar, margin, framerate), so the look can be swapped without touching the run; `demo/sample.tape` is the sequence.

The password typed on screen belongs only to the throwaway demo container and is discarded with it. For setup and usage, see the [main README](../../README.md).
