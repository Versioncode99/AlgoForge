"""No colour outside the token file, and every theme complete.

A theme system is only real if a theme change actually changes something. One
literal `#3a2523` in a stylesheet is a border that stays the same colour in all
three themes, and it will not look broken — it will look like one panel that
somebody forgot, which is exactly the kind of defect that survives a review.

So the rule is mechanical: `tokens.css` is the only stylesheet allowed to name a
colour. Everything else says `var(--something)` or mixes one.

The second half of the file checks the palettes are complete. A theme that
redefines eleven of twelve surface tokens inherits the twelfth from Premium
Graphite, which on a light ground means one panel of near-black.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STYLES = Path(__file__).resolve().parents[2] / "apps" / "web" / "src" / "styles"
TOKENS = STYLES / "tokens.css"
WEB_SRC = Path(__file__).resolve().parents[2] / "apps" / "web" / "src"

#: A literal colour: hex, or a legacy rgb()/rgba() with numeric channels. The
#: modern `rgb(0 0 0 / 0.2)` form used inside tokens.css for shadow and glass is
#: matched too, and is why this only runs outside that file.
LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(\s*\d")

STYLESHEETS = sorted(path for path in STYLES.glob("*.css") if path.name != "tokens.css")


def strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


@pytest.mark.parametrize("path", STYLESHEETS, ids=lambda p: p.name)
def test_no_stylesheet_but_tokens_names_a_colour(path: Path) -> None:
    body = strip_comments(path.read_text("utf-8"))
    offenders = sorted(set(LITERAL.findall(body)))
    assert not offenders, (
        f"{path.name} names colours directly: {offenders}. "
        "A literal is a value that cannot change with the theme — use a token, "
        "or color-mix() one over a surface."
    )


def test_tokens_css_is_the_only_place_that_does() -> None:
    """Stated as its own test so the rule reads as a rule, not an exception."""
    assert LITERAL.search(strip_comments(TOKENS.read_text("utf-8"))), (
        "tokens.css defines the palette; if it stopped naming colours something "
        "has gone badly wrong"
    )


# ── every theme is complete ──────────────────────────────────────────────────


def block(selector: str) -> dict[str, str]:
    """The custom properties declared in one block of tokens.css."""
    text = TOKENS.read_text("utf-8")
    start = text.index(selector)
    open_brace = text.index("{", start)
    close = text.index("\n}", open_brace)
    body = strip_comments(text[open_brace + 1 : close])
    return {
        name.strip(): value.strip()
        for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body)
    }


ROOT = block(":root {")
THEME_BLOCKS = {
    "silver": ':root[data-theme="silver"]',
    "contrast": ':root[data-theme="contrast"]',
}

#: Everything a theme has to restate. A colour inherited from the default
#: palette is a near-black panel in the middle of a light theme.
COLOUR_PREFIXES = (
    "--bg-",
    "--line",
    "--fg-",
    "--brand",
    "--accent",
    "--pass",
    "--fail",
    "--warn",
    "--info",
    "--neutral",
    "--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8",
    "--verdict-",
    "--tier-",
    "--chart-",
)


def colour_tokens() -> set[str]:
    return {
        name
        for name in ROOT
        if any(name.startswith(prefix) for prefix in COLOUR_PREFIXES)
    }


@pytest.mark.parametrize("theme", sorted(THEME_BLOCKS))
def test_every_theme_redefines_every_colour(theme: str) -> None:
    declared = set(block(THEME_BLOCKS[theme]))
    missing = sorted(colour_tokens() - declared)
    assert not missing, f"{theme} inherits {missing} from the default palette"


@pytest.mark.parametrize("theme", sorted(THEME_BLOCKS))
def test_a_theme_only_moves_colour(theme: str) -> None:
    """Geometry, type and spacing belong to the layout, not to the palette.

    A theme that moved a spacing step would make "light" also mean "looser",
    which is what density is for and what makes the two separate attributes.
    """
    declared = set(block(THEME_BLOCKS[theme]))
    structural = {
        name
        for name in declared
        if name.startswith(("--sp-", "--fs-", "--row-", "--r-", "--font-", "--lh", "--t-"))
    }
    assert not structural, f"{theme} changes layout tokens: {sorted(structural)}"


def test_density_only_moves_layout() -> None:
    """And the mirror image: density must not touch a colour."""
    declared = block(':root[data-density="comfortable"]')
    coloured = {
        name for name in declared if any(name.startswith(p) for p in COLOUR_PREFIXES)
    }
    assert not coloured, f"density changes colours: {sorted(coloured)}"


def test_density_does_not_change_the_type_scale() -> None:
    """Bigger labels is how a dense workstation becomes a spreadsheet."""
    declared = block(':root[data-density="comfortable"]')
    assert not {name for name in declared if name.startswith("--fs-")}


def test_reduced_motion_collapses_every_duration() -> None:
    declared = block(':root[data-motion="reduced"]')
    for name in ("--t-fast", "--t", "--t-slow"):
        assert name in declared, f"{name} still animates under reduced motion"
        assert declared[name].endswith("ms")
        assert float(declared[name].removesuffix("ms")) <= 1.0


# ── glass stays a material ───────────────────────────────────────────────────


#: Every surface in the application allowed to be translucent, and why.
#: The brief puts glass on floating toolbars, modal surfaces and transient
#: inspectors; these are those three and nothing else. The list is short on
#: purpose — the failure mode is glass spreading to docked panels, where it
#: costs contrast and buys nothing, and to charts, where it costs the one thing
#: a chart cannot spend.
GLASS_SURFACES = {
    ".af-glass": "the material itself: command palette, trade inspector, lab drilldown",
    ".topbar": "a floating toolbar over scrolling content",
    ".palette-backdrop": "the scrim behind a modal",
}


def blurred_selectors() -> list[str]:
    """Every selector in the stylesheets that declares a backdrop blur.

    `@supports` preludes are stripped first: the feature query that provides the
    no-blur fallback names `backdrop-filter` in its own condition, which is a
    test for the property rather than a use of it.
    """
    found: list[str] = []
    for path in STYLES.glob("*.css"):
        body = strip_comments(path.read_text("utf-8"))
        body = re.sub(r"@supports[^{]*", "@supports ", body)
        for match in re.finditer(r"backdrop-filter\s*:", body):
            brace = body.rfind("{", 0, match.start())
            if brace == -1:
                continue
            previous = max(body.rfind("}", 0, brace), body.rfind("{", 0, brace))
            selector = body[previous + 1 : brace].strip()
            found.append(f"{path.name}: {selector}")
    return found


def test_translucency_is_confined_to_a_named_short_list() -> None:
    offenders = [
        entry
        for entry in blurred_selectors()
        if not any(name in entry for name in GLASS_SURFACES)
    ]
    assert not offenders, (
        f"blur outside the allowed surfaces: {offenders}. "
        f"Allowed: {sorted(GLASS_SURFACES)}"
    )


def test_something_is_actually_translucent() -> None:
    """So the rule above cannot pass by there being no glass at all."""
    assert blurred_selectors()


def test_every_blur_is_themeable() -> None:
    """A literal blur radius is one the high-contrast theme cannot turn down.

    That theme exists to protect separation, and it reduces the blur levels to
    almost nothing. A rule with `blur(8px)` written in would ignore it.
    """
    offenders: list[str] = []
    for path in STYLES.glob("*.css"):
        body = strip_comments(path.read_text("utf-8"))
        body = re.sub(r"@supports[^{]*", "@supports ", body)
        for match in re.finditer(r"backdrop-filter\s*:\s*([^;]+);", body):
            if "--blur-" not in match.group(1):
                offenders.append(f"{path.name}: {match.group(1).strip()}")
    assert not offenders, f"hard-coded blur radius: {offenders}"


@pytest.mark.parametrize("theme", sorted(THEME_BLOCKS))
def test_the_high_contrast_theme_reduces_translucency(theme: str) -> None:
    """Only `contrast` has to, but the assertion is written for whichever does."""
    declared = block(THEME_BLOCKS[theme])
    if theme != "contrast":
        return
    for name in ("--blur-1", "--blur-2", "--blur-3"):
        assert name in declared, f"contrast does not reduce {name}"
        assert float(declared[name].removesuffix("px")) < float(
            ROOT[name].removesuffix("px")
        )


def test_no_chart_component_is_given_glass() -> None:
    for path in WEB_SRC.rglob("*.tsx"):
        source = path.read_text("utf-8")
        for line in source.splitlines():
            if "af-glass" not in line:
                continue
            assert "chart" not in line.lower(), f"{path.name} puts glass on a chart: {line.strip()}"


# ── one palette, in one file ─────────────────────────────────────────────────


#: Layout values a stylesheet may legitimately re-declare inside a media query —
#: a topbar that wraps at 640px genuinely is a different height. Colour is never
#: on this list.
LAYOUT_OVERRIDABLE = {"--topbar-h", "--logbar-h", "--rail-w", "--row-h", "--pad-panel"}


def declared_tokens(path: Path) -> set[str]:
    body = strip_comments(path.read_text("utf-8"))
    return {name for name, _ in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body)}


@pytest.mark.parametrize("path", STYLESHEETS, ids=lambda p: p.name)
def test_only_the_token_file_defines_the_palette(path: Path) -> None:
    """A second `:root` palette is a second design system wearing a stylesheet.

    `command.css` opened with one: background, text, line and brand redefined,
    loaded after the token file and therefore winning. The token file could be
    edited with no visible effect, and the detokeniser rewrote that block's
    literals into `--bg-0: var(--bg-0)` — circular, invalid, and enough to
    unset the palette for the whole application.

    Layout values may be re-declared, because a topbar that wraps at 640px is
    genuinely a different height. Colour may not.
    """
    offenders = sorted(
        name
        for name in declared_tokens(path)
        if any(name.startswith(prefix) for prefix in COLOUR_PREFIXES)
        and name not in LAYOUT_OVERRIDABLE
    )
    assert not offenders, (
        f"{path.name} defines palette tokens {offenders}. "
        "The palette lives in tokens.css; a stylesheet that redefines one is a "
        "second design system."
    )


# ── the shell's geometry is tokenised too ────────────────────────────────────


#: A pixel radius written into a rule, e.g. `border-radius: 7px`. Percentages
#: and `50%` are fine -- a circle is a shape, not a scale step -- and so is a
#: `var()`, which is the whole point.
PIXEL_RADIUS = re.compile(r"border-radius\s*:\s*[^;]*?\d+px")

#: The shell stylesheet. Held to the same rule as colour for the same reason:
#: a radius written into a rule is one that density and future themes cannot
#: reach, and the shell is where the four-step scale has to be legible because
#: it is the file every other stylesheet is read against.
#:
#: The view stylesheets still carry literal radii and are deliberately not in
#: scope here -- converting them is a separate change, and a test that fails on
#: work nobody has started is a test that gets deleted.
SHELL = STYLES / "workstation.css"


def test_the_shell_names_no_literal_radius() -> None:
    body = strip_comments(SHELL.read_text("utf-8"))
    offenders = sorted(set(PIXEL_RADIUS.findall(body)))
    assert not offenders, (
        f"{SHELL.name} writes radii as literals: {offenders}. "
        "Use one of --r-sm / --r / --r-lg / --r-round; a literal is a corner "
        "that no density or theme can move."
    )


def test_the_shell_actually_rounds_things() -> None:
    """So the rule above cannot pass by the shell having no corners at all.

    Before the overhaul this file declared exactly one radius in sixty-nine
    lines, which is how a workstation ends up reading as a terminal emulator.
    """
    body = strip_comments(SHELL.read_text("utf-8"))
    assert body.count("border-radius") >= 15


def test_no_token_is_defined_in_terms_of_itself() -> None:
    """`--bg-0: var(--bg-0)` is silently invalid and unsets the whole palette."""
    for path in STYLES.glob("*.css"):
        body = strip_comments(path.read_text("utf-8"))
        for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body):
            assert f"var({name})" not in value, f"{path.name}: {name} references itself"
