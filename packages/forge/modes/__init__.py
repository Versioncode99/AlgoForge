"""The four operating environments: what they are, and who may do what inside them.

One platform, four modes. `models` declares them, `permissions` decides what an
AI actor may do in each, and `store` remembers which one is open and what each
one had on screen.

Two more declarations sit beside them because they answer the same kind of
question and have to be answerable without a person: `expertise` says how much
of the machinery each disclosure level shows, and `intents` maps "what do you
want to do?" onto registered actions rather than onto a language model.
"""

from forge.modes.expertise import (
    ALWAYS_VISIBLE,
    LEVEL_LABEL,
    ExpertiseLevel,
    LevelDescriptor,
    Surface,
    shows,
    visible,
)
from forge.modes.expertise import (
    catalogue as expertise_catalogue,
)
from forge.modes.expertise import (
    descriptor as expertise_descriptor,
)
from forge.modes.intents import (
    INTENTS,
    Intent,
    IntentDescriptor,
    for_mode,
)
from forge.modes.intents import (
    catalogue as intent_catalogue,
)
from forge.modes.intents import (
    descriptor as intent_descriptor,
)
from forge.modes.models import (
    MODE_ORDER,
    MODES,
    ModeDescriptor,
    Section,
    Stance,
    WorkspaceMode,
    catalogue,
    descriptor,
    parse_stance,
)
from forge.modes.permissions import (
    AUTOMATION,
    CONSEQUENTIAL,
    PREPARATORY,
    ActionFacts,
    Actor,
    Judgement,
    Ruling,
    evaluate,
    summarise,
)
from forge.modes.store import SCHEMA_VERSION, ModeSession, ModeStore

__all__ = [
    "ALWAYS_VISIBLE",
    "AUTOMATION",
    "CONSEQUENTIAL",
    "INTENTS",
    "LEVEL_LABEL",
    "MODES",
    "MODE_ORDER",
    "PREPARATORY",
    "SCHEMA_VERSION",
    "ActionFacts",
    "Actor",
    "ExpertiseLevel",
    "Intent",
    "IntentDescriptor",
    "Judgement",
    "LevelDescriptor",
    "ModeDescriptor",
    "ModeSession",
    "ModeStore",
    "Ruling",
    "Section",
    "Stance",
    "Surface",
    "WorkspaceMode",
    "catalogue",
    "descriptor",
    "evaluate",
    "expertise_catalogue",
    "expertise_descriptor",
    "for_mode",
    "intent_catalogue",
    "intent_descriptor",
    "parse_stance",
    "shows",
    "summarise",
    "visible",
]
