# Hedge Fund capabilities, reclassified

Doc 2 §1 removes the Hedge Fund product mode and is explicit that this must not
mean deleting the functionality: *"REMOVE THE HEDGE FUND LABEL / RETAIN VALUABLE
CAPABILITIES / MOVE THEM TO THE CORRECT PRODUCT SURFACE"*, with every capability
classified into exactly one of seven buckets.

The mode was removed in `32f2445`. This is the classification that should have
accompanied it, written against the code rather than from memory. It changes
nothing on its own — every move it records has already happened — and it exists
so the moves are answerable rather than asserted.

## The buckets, as the directive names them

`RETAIN-AS-CORE` · `MOVE-TO-WORKSTATION-PANEL` · `MOVE-TO-PROP-DESK` ·
`MOVE-TO-RESEARCH` · `MOVE-TO-RISK/EXECUTION` · `WORKSPACE-TEMPLATE-ONLY` ·
`REMOVE`

## The eleven-stage loop — `forge.hedgefund.loop`

The loop was the mode's spine: eleven stages from data to feedback, each with a
status that can be UNKNOWN rather than assumed. Nothing about it was
hedge-fund-specific; it is the shape of running a book, and somebody trading
their own is running one.

| Stage | Bucket | Where it lives now |
| --- | --- | --- |
| Data | **RETAIN-AS-CORE** | `normal/data` — the data workspace |
| Research | **MOVE-TO-RESEARCH** | `research`, `lab`, campaigns |
| Alpha | **MOVE-TO-RESEARCH** | Strategy library and the frontier |
| Validation | **RETAIN-AS-CORE** | `validation` — the gate ladder is not optional anywhere |
| Portfolio | **RETAIN-AS-CORE** | `normal/portfolio` |
| Risk | **MOVE-TO-RISK/EXECUTION** | `normal/risk` |
| Pre-Trade | **MOVE-TO-RISK/EXECUTION** | `normal/gate` — G0–G13 |
| Execution | **MOVE-TO-RISK/EXECUTION** | `normal/execution` |
| Operations | **MOVE-TO-RISK/EXECUTION** | `normal/operations` |
| Performance | **RETAIN-AS-CORE** | `normal/performance` |
| Feedback | **MOVE-TO-RESEARCH** | Re-evaluation feeds the frontier |

**`LOOP` and `STAGES` themselves are RETAIN-AS-CORE.** The stage vocabulary is
still the thing the book views are arranged around; only the mode that used to
own it is gone.

## The oversight surfaces

| Capability | Module | Bucket | Where it lives now |
| --- | --- | --- | --- |
| Approval queue — AI-proposed actions waiting for a person | `hedgefund/approvals.py` | **RETAIN-AS-CORE** | `ai/approvals`. It moved to the AI mode because that is where the actor it exists to watch actually is; it is core rather than AI-specific, and a second queue would be a second place to forget to look. |
| Audit log — the answerable record | `hedgefund/audit.py` | **RETAIN-AS-CORE** | `ai/audit`, and also read by `propdesk/audit.py`. Two consumers is the argument against moving it into either. |
| Fund configuration — restrictions and limits | `hedgefund/config.py` | **RETAIN-AS-CORE** | Still `set_fund_config`, still `protected`, still denied to an AI actor in every mode. |

## What the classification does *not* license

**`REMOVE` is empty, and that is a finding rather than a formality.** Nothing
built for the Hedge Fund mode turned out to be hedge-fund-specific. The mode was
a label over capabilities that a prop trader and a systematic trader both need,
which is precisely why removing the label cost nothing and why the directive was
right that deleting the functionality would have been the wrong reading.

**`WORKSPACE-TEMPLATE-ONLY` has one entry:** the institutional layout survives as
the `Book Command` workspace template. A template is a starting point, not a
locked mode — which is the whole mechanism by which the institutional arrangement
outlives the product category.

**`MOVE-TO-WORKSTATION-PANEL` has none.** Every capability above is a *surface*
with its own route rather than a panel in somebody's layout. Forcing them into
panels to fill the bucket would have been complexity added to satisfy a
classification, which Doc 2 §26 names directly.

## The one thing the module name still gets wrong

The package is still called `forge.hedgefund` while the product concept it was
named for no longer exists. That is honest about its history and misleading about
its contents — `approvals`, `audit`, `config` and `loop` are the book layer, and
nothing in them is about hedge funds.

**Not renamed here, deliberately.** It is a pure rename touching six import
sites, with no behavioural change and no test that would catch a mistake in the
middle of it, and it would sit in a commit whose subject is a classification. It
is the kind of change that deserves to be the only thing in its own diff.
Recorded as a known naming debt rather than done in passing.
