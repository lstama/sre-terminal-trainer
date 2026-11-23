# demo/style.tape - presentation settings for the demo recording.
#
# Everything visual lives in this one file, so the look can be swapped without
# touching demo/sample.tape (which only drives the real harness).  It is
# sourced by demo/sample.tape and by demo/render.sh, which copies it next to
# the generated tape because VHS resolves `Source` relative to the working
# directory.
#
# Themes come from `vhs themes`; "Catppuccin Frappe" is the one this demo was
# rendered with (Frappe base #303446 is reused as the margin fill).

Set Theme "Catppuccin Frappe"

# Typography and geometry.  20pt keeps a ~105-column terminal legible at 1280px.
Set FontSize 20
Set Width 1280
Set Height 800
Set Padding 20

# Framing: colourful window chrome on a solid margin, soft corners.
Set WindowBar Colorful
Set WindowBarSize 28
Set Margin 24
Set MarginFill "#303446"
Set BorderRadius 8

# Timing: 30 fps, normal speed, brisk typing so the video does not drag.
Set Framerate 30
Set PlaybackSpeed 1
Set TypingSpeed 40ms
