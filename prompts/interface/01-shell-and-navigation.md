# 01 — The shell and its navigation

Reference study: QuantPad's project workspace, Quantower's panel model.

<!-- prompt:start -->
Rebuild the application shell so navigation is the calmest thing on screen
rather than the loudest. Today the rail paints every destination as a
full-bleed row separated by hairlines, marks the current one with a two-
pixel left border, and sets its group headings in ten-pixel uppercase
mono. Twenty destinations rendered that way read as a directory listing,
which is why moving between them feels like scanning rather than choosing.

Adopt the shape QuantPad uses and Quantower's toolbar shares. Each
destination becomes a discrete rounded row inset from the rail edge, with
its own hit area, an icon at a readable size, and a label in the interface
face rather than the monospace one. Reserve monospace for figures. The
current destination is filled, not bordered: a soft wash of the accent
carrying the foreground up to full strength. Hover is a lighter fill of
the same family, so hover and selection read as two steps of one idea
instead of two unrelated treatments. Drop the hairline between groups; let
the group label and the spacing above it do that work, because a rule
between every five items is nineteen more lines than the eye needs.

The rail collapses to icons and must stay usable collapsed: every row
keeps an accessible name, the tooltip appears without waiting on the title
attribute's delay, and the active fill still reads at icon width. The
collapse control belongs beside the wordmark and states which direction it
goes.

Keep the context bar to one row: where you are, then the facts that change
—connection, data gate, counts—as quiet chips, then search. Give the
search control the keyboard hint inline, the way QuantPad prints Ctrl+B
next to the file-tree toggle, because a shortcut nobody sees is a
shortcut nobody uses. Nothing here should animate on load.
<!-- prompt:end -->
