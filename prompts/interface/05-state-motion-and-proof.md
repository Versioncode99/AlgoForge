# 05 — State, motion, and proving the overhaul landed

Reference study: TradesAI's approve-or-automate posture; Quantower themes.

<!-- prompt:start -->
An interface overhaul that only changes how things look is a repaint. This
one has to change what the shell tells you, and it has to be checkable.

Start with the states the shell currently gets wrong. When the API is
unreachable every view is gated off and the workspace goes blank behind a
single error strip, so a momentary blip reads as a lost application. Keep
the strip, but let it say what is unreachable and offer the retry, and
hold the chrome in place so there is something to come back to. The
connection, data gate and count chips are the shell's only live telemetry:
give them a shape that distinguishes a real state from an unknown one,
because a count of zero and a count never fetched are different facts and
currently look identical.

The client turns any non-JSON error body into a parse exception that
escapes the error type the rest of the application catches. A proxy's HTML
error page or an empty gateway response therefore surfaces as an
unexpected token message with no status attached. Parse defensively and
preserve the status.

Motion stays informational. Transitions belong on the rail collapse, the
drawer, and hover and selection changes, at the durations already
tokenised; nothing animates on first paint, and both the reduced-motion
setting and the operating-system preference must still collapse every
duration to nothing.

Then prove it. The stylesheet tests already refuse a literal colour
outside the token file and refuse a second palette; extend that discipline
with a test that refuses a literal pixel radius in the shell, and a test
that the skip target and the route identifiers cannot collide. Run the
existing Playwright checks at every breakpoint they cover, in all three
themes, and report what actually rendered rather than what should have.
<!-- prompt:end -->
