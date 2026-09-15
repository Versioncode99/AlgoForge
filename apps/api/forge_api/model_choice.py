"""One resolver, called by everything that needs a model.

**What this replaces.** Three different ways of answering "which model?".
`AgentService` already called `model_routing.resolve` and recorded the decision.
`Assistant.ask` read `settings.ai.routing.get("chat", "")` — the flat mapping —
and the orchestrator read `settings.ai.routing.get("orchestrator", "")`. The
flat mapping is a *copy* of the routing that `control.py` kept in step by hand,
so a feature override, a fallback, a role-level disable and every reason string
were invisible to two of the three callers: an operator could set the chat model
in Settings, watch the rich table update, and have the conversation answered by
whatever the flat copy still said.

So the copy stops being read. `choose` is the single path from settings to a
model, it returns the `Decision` with its reason attached, and
`tests/api/test_model_routing_consumers.py` asserts that no caller reaches
around it.

It lives here rather than in `model_routing` because that module deliberately
knows nothing about providers — no catalogue, no credential, no client — and
joining the two is exactly what this file is for.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from forge_api.model_routing import Decision, resolve
from forge_api.providers import catalog_for, credential_for, model_for


def choose(
    settings: Any, role: str, *, unavailable: Iterable[str] = ()
) -> Decision:
    """The model for one role, with the reason it was that one.

    `settings` is a `forge_api.settings_store.Settings`. Typed loosely on
    purpose: `settings_store` imports `model_routing`, and naming its `Settings`
    here would close a circle for one annotation.

    The provider's own repair runs last. `providers.model_for` exists because
    routing is stored per role rather than per provider, so switching provider
    leaves roles pointing at models the new one does not serve — real models,
    which nothing rejected at save time. Applying it after the decision keeps
    the *reason* honest: the decision says what routing chose, and a
    substitution the provider made is visible as the model differing from it.
    """
    decision = resolve(
        role,
        settings.ai.model_routing,
        provider=settings.ai.provider,
        catalogue=catalog_for(settings.ai.provider),
        unavailable=unavailable,
    )
    if not decision.model:
        return decision
    served = model_for(settings.ai.provider, decision.model)
    if served == decision.model:
        return decision
    return Decision(
        role=decision.role,
        provider=decision.provider,
        model=served,
        source=decision.source,
        reason=(
            f"{decision.reason} The {settings.ai.provider} provider does not serve "
            f"'{decision.model}', so '{served}' answered."
        ),
        substituted=True,
        considered=(*decision.considered, decision.model),
    )


def reachable(settings: Any) -> bool:
    """Whether a model call can be made at all, before choosing which one.

    Three separate facts, and they fail differently: AI switched off is a
    choice, a missing credential is a setup step, and an unserved model is a
    routing problem. Only the first two are answered here — the third is
    `choose`'s, and it answers with a reason rather than a boolean.
    """
    return bool(settings.ai.enabled) and credential_for(settings.ai.provider).present
