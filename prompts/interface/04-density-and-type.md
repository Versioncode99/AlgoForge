# 04 — Density, type and the dead stylesheet

Reference study: Quantower's table density; QuantPad's editor-chat balance.

<!-- prompt:start -->
A research workstation has to stay dense, so the goal is not more space —
it is spending the space that exists on legibility instead of on rules and
uppercase. Three problems compound in the current build.

First, the shell sets its own type inline: eight- and nine-pixel monospace
with wide letter-spacing for the context facts, the status bar, the group
headings and the drawer. The token file already declares a type scale
whose smallest step is ten pixels, chosen deliberately as the floor at
which a label is readable at arm's length, and the shell undercuts it
everywhere. Route every size through the scale, and keep monospace for
figures, hashes, identifiers and timestamps only. A label is prose.

Second, roughly a third of the main stylesheet styles a shell that no
longer exists. The topbar, the tab strip, the old rail rows, the log bar
and the log lines are all fully specified and nothing renders any of them
— the application replaced that chrome and the styling was left behind.
The real shell got sixty-nine lines, written one rule per line, while a
hundred and fifty lines of polish sit on markup that was deleted. Remove
all the dead rules; the confusion they cause is the reason the live shell
never got the same care.

Third, reformat the live stylesheet so it can be edited. Rules crammed
several to a line are why it has not been maintained.

Then spend the recovered space: taller rail rows, real padding inside the
context bar's chips, a status bar that is not a nine-pixel strip. Keep
both density modes working — comfortable moves spacing and row height and
must still leave the type scale alone, because larger labels is how a
dense workstation becomes a spreadsheet with fewer rows on it.
<!-- prompt:end -->
