"""What ships, what was generated, and what a refusal says about the difference.

`TEMPLATES` is deliberately open. `TemplateStore` registers what an operator
authored, the research director registers what a campaign composed, and at the
point of use a registered template is meant to be indistinguishable from a
shipped one. That is the right property for the engine.

It is the wrong property for a *sentence shown to somebody who got the name
wrong*, and the difference was a real bug. `AgentService.propose` answered an
unknown template with ``", ".join(sorted(TEMPLATES)[:12])``. On a fresh process
that is the whole shipped catalogue and reads correctly. After a campaign has
run, the dictionary holds hundreds of generated variants, twelve alphabetically
first entries are all machine-named ones like
``displacement_reversion_a1b2``, and the refusal names not one template the
proposer was ever meant to ask for. The same listing is what
`tests/api/test_agent_proposals.py` asserts on, and it failed for exactly that
reason in a full run and passed in isolation -- an order-dependent failure whose
cause was the production message, not the test.

So the catalogue answers two different questions with two different functions.
`SHIPPED_TEMPLATE_KEYS` is captured here, once, before anything can register at
run time, and the refusal below leads with it and *counts* the rest rather than
truncating into the middle of it.
"""

from __future__ import annotations

import difflib

from forge.strategy.templates import TEMPLATES
from forge.strategy.templates_quant import QUANT_TEMPLATES
from forge.strategy.templates_statistical import STATISTICAL_TEMPLATES

# Session-aware families are registered into the same dict every consumer holds.
# This is the one place it happens, so the snapshot below cannot be taken before
# the catalogue is complete.
TEMPLATES.update(QUANT_TEMPLATES)
TEMPLATES.update(STATISTICAL_TEMPLATES)

#: The templates this build ships, captured before run-time registration.
#:
#: Identical in capability to a generated one, distinguishable in provenance --
#: which is what a test asserting something about what this repository ships
#: needs, and what a refusal shown to a proposer needs.
SHIPPED_TEMPLATE_KEYS: frozenset[str] = frozenset(TEMPLATES)


def known_templates(limit: int = 12) -> list[str]:
    """Templates to name in a message, shipped first and in a stable order.

    Stable across a run: the shipped block cannot be displaced by whatever a
    campaign happened to generate, so two readers of the same refusal see the
    same names. Generated keys fill any remaining room in sorted order, which
    keeps the listing deterministic without pretending the two kinds are
    interchangeable to a reader.
    """
    shipped = sorted(SHIPPED_TEMPLATE_KEYS & set(TEMPLATES))
    if len(shipped) >= limit:
        return shipped[:limit]
    generated = sorted(set(TEMPLATES) - SHIPPED_TEMPLATE_KEYS)
    return shipped + generated[: limit - len(shipped)]


def unknown_template(requested: str, *, limit: int = 12) -> str:
    """The sentence a proposer gets back when the template name is not a template.

    Three facts, because three facts are what would let the reader fix it: what
    they asked for, the nearest thing that exists if there is one, and names
    they could have meant. The count of what is not listed is stated rather than
    elided -- "and 602 generated variants" is information; a trailing ellipsis
    over a truncated list is the absence of it.
    """
    names = known_templates(limit)
    unlisted = len(TEMPLATES) - len(names)
    close = difflib.get_close_matches(requested, sorted(TEMPLATES), n=1, cutoff=0.7)
    suggestion = f" Did you mean '{close[0]}'?" if close else ""
    tail = (
        f" ({unlisted} further template{'s' if unlisted != 1 else ''} generated in this "
        "workspace are not listed; call `list_templates` for the whole catalogue.)"
        if unlisted > 0
        else ""
    )
    return f"Unknown template '{requested}'.{suggestion} Available: {', '.join(names)}.{tail}"
