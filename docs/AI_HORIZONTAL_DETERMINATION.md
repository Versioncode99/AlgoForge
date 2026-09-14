# Why AI is still a mode, and in what sense it is already horizontal

Doc 2 §2 wants AI to be a horizontal capability rather than a separate operating
environment. The reconciliation directive asks for the implementation to be
inspected before the label is removed, and is explicit about the risk:

> *"if the current 'AI mode' is actually the security/permission boundary that
> controls assistant capabilities, inspect the implementation before changing
> it. Do not destabilize a correct permission architecture merely to remove a
> label."*

This is that inspection. Its conclusion is that the **capability is already
horizontal** and the **mode is the permission boundary**, and that the two are
separable in the product and not in the policy.

## The capability is horizontal, and is checked

Every mode's manifest lists the `assistant` section, and every mode's workspace
sections include the `agent` panel kind. The action registry is one registry:
there is no AI-only verb and no mode-specific implementation of any action — the
interface and an assistant call the same `Actions.call`. A conversation opened
in Prop Firm reaches the same strategies, the same judge and the same data as one
opened in Normal.

`tests/modes/test_ai_horizontal.py` asserts all of that, so the claim fails if it
stops being true rather than ageing quietly in this document.

## The mode is the boundary, and removing it removes the boundary

`forge.modes.permissions.evaluate` is a pure function of four facts — actor,
mode, stance, action — and **two of its nine rules read the mode**:

| Rule | What it decides |
| --- | --- |
| 7 — automation | An `AUTOMATION` action is `ALLOW` in AI mode and `REQUIRE_APPROVAL` in every other. Starting unattended work is the grant. |
| 8 — the book | A `CONSEQUENTIAL` action is `ALLOW` only in AI mode **and** on the autonomous stance. Everything else holds it for a person. |

`Stance` exists only in AI mode, for the reason its own docstring gives: the
distinction has nothing to say in an environment whose purpose is not unattended
work.

So "AI mode" is not a label over a menu. It is the name of the state in which an
assistant may start work nobody is watching, and — with the stance — the state in
which it may reach the book. Delete the mode and those two grants have nowhere to
attach.

## What removing it would actually mean

Making AI horizontal *in the policy* means replacing "which mode am I in" with an
orthogonal autonomy setting, so that Normal-plus-autonomous and
Prop-Firm-plus-autonomous become expressible. That is not a rename: it is a new
axis in the permission model, and it **widens** the reachable set in exactly the
direction the directives forbid —

> Doc 1 §37: *"Safe default must remain deny/protected. Do not solve action
> coverage by making the default permissive."*

Today an operator working in Prop Firm cannot, by any setting, have an assistant
submit an order unattended. Under an orthogonal autonomy flag they could. That is
a larger security decision than a product-taxonomy one, and it is not this
phase's to make on its own.

## The honest summary

| Claim | State |
| --- | --- |
| AI's *capability* is available in every mode | **True, and tested** |
| AI is a separate product environment on the mode chooser | **True** — and it is the permission boundary, not a category |
| AI could be made horizontal without weakening the boundary | **Not without adding an autonomy axis**, which widens what is reachable |

Recorded as **INTENTIONALLY_REJECTED with the architectural reason**, which is the
outcome the reconciliation directive names when the boundary is the reason. It is
not scored complete, and it is not scored missing.
