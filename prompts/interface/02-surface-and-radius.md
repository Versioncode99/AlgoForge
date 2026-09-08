# 02 — The surface system and the radius scale

Reference study: QuantPad's card treatment, Linear/Cursor-lineage AI IDEs.

<!-- prompt:start -->
The token file already declares a radius scale and the shell ignores it
completely: the rail, the context bar, the status bar, the event drawer,
the search controls and the context chips are all hard rectangles. That is
the largest reason the interface reads as a terminal emulator, not a
workstation. Fix it as a system, not as a pass of rounded corners over
whatever looks sharp.

Define four radii and give each one a job. A small radius for controls
that sit inside something else — chips, pills, table affordances, inline
badges. A medium radius for the ordinary docked surface: panels, cards,
rail rows, inputs, buttons. A large radius for surfaces that float above
the workspace: the command palette, the event drawer, the trade inspector.
A full round for status dots and count bubbles only. A component that
cannot say which of the four it is has not been designed yet.

Nesting rule: a child's radius is always smaller than its parent's, and
the difference should approximate the padding between them, or the corners
peel. A panel at the medium radius holding chips at the small one is
correct; a chip at the same radius as the panel it sits in is not.

Elevation stays borrowed from the existing three-step shadow scale, and
docked surfaces still get a hairline border rather than a shadow — a
shadow on something that cannot move is decoration. Only floating surfaces
earn the third step.

Critically: every value here is a token in the token file, because the
test suite refuses a literal colour in any other stylesheet and geometry
deserves the same discipline. A radius written into a rule is one that
density and future themes cannot reach. Add the tokens; reference them
everywhere; leave no rule naming a pixel radius directly.
<!-- prompt:end -->
