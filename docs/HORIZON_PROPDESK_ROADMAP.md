# Prop Desk: what is built, what is declared, and what is neither

Classification as of the Horizon branch. The point of writing it down is that
"prop desk support" is four different claims, and a roadmap that does not
separate them is the mechanism by which a reader concludes the wrong one.

Each row is one of:

| Label | Means |
|---|---|
| **BUILT** | Implemented, tested, and reachable from the interface. |
| **BUILT, READ-ONLY** | Implemented and reachable; cannot send an order. |
| **DECLARED** | The interface is described in data. No code executes it, and every command is refused with the reason. |
| **BLOCKED** | Implemented as far as it can be here. What remains is somebody else's decision — an approval, an agreement, a credential. |
| **NOT BUILT** | Neither implemented nor declared. Named so its absence is visible. |

---

## Rule evaluation

| Capability | State | Where |
|---|---|---|
| Rule set as data, no firm named in code | **BUILT** | `forge.prop.account` |
| Static / end-of-day / intraday trailing floors | **BUILT** | `TrailMode`, `forge.prop.account` |
| Daily loss, profit target, position and order caps | **BUILT** | `AccountRules` |
| Minimum trading days, consistency share | **BUILT** | `AccountRules` |
| Custom limits over observed metrics only | **BUILT** | `CustomLimit` |
| `NOT_ASSESSED` for a rule that cannot be evaluated | **BUILT** | `forge.prop.account.assess` |
| Distributional pass-rate simulation | **BUILT** | `forge.prop.engine` |
| Rule sets loaded from disk | **BUILT** | `forge.prop.catalogue` — new in Horizon |
| Versioned rule schema, unknown versions refused | **BUILT** | `SCHEMA_VERSION`, `catalogue.parse` |
| Provenance: source, who checked, when it expires | **BUILT** | `RuleProvenance` |
| Review that lapses on its own | **BUILT** | `Verification.EXPIRED` |
| Payout terms enforced as trading limits | **NOT BUILT**, deliberately | A payout threshold is not a trading limit. Enforcing one would refuse trades no contract refuses. Recorded on the rule set's note. |
| Fetching a firm's terms and diffing them | **NOT BUILT** | Would mean scraping contracts and asserting what they say. The provenance record holds a URL and a hash the operator captured; nothing fetches either. |

### The one thing this layer will not do

Assert what any named firm's contract says. `provider` is free text the operator
typed, nothing branches on it, and `tests/propdesk/test_prop_catalogue.py`
asserts the module names no firm. The four files in `rules/` are samples and
report themselves as `UNVERIFIED` — they load, and loading them does not make
them checked.

---

## Execution

| Capability | State | Where |
|---|---|---|
| Local simulator | **BUILT** | `forge.propdesk.adapters.simulated` — labels every fill simulated |
| Order lifecycle state machine | **BUILT** | `forge.propdesk.orders` |
| Deterministic idempotency keys | **BUILT** | `forge.propdesk.orders.idempotency_key` |
| Pre-trade gate | **BUILT** | `forge.execution.gate` |
| Copy relationships, one owner attested | **BUILT** | `forge.propdesk.copy` |
| Allocation across accounts | **BUILT** | `forge.propdesk.scaling` |
| Rithmic: connect, discover systems and accounts, snapshot | **BUILT, READ-ONLY** | `forge.propdesk.rithmic` — needs the operator's SDK |
| Rithmic: WebSocket transport | **BUILT** | `rithmic.transport`, tested against a real socket |
| Rithmic: vocabulary from the operator's `.proto` files | **BUILT** | `rithmic.vocabulary` |
| Rithmic: place / modify / cancel / flatten | **BLOCKED** | Translated and tested; refuses to send. A first order in Rithmic Test is a step a person confirms, naming the account, instrument, side, quantity, order type and how it will be cancelled. |
| Rithmic: verified against Rithmic Test | **BLOCKED** | Needs a Test credential and a prior interactive login through R\|Trader to accept the agreements. Neither may be bypassed. |
| Rithmic: production | **BLOCKED** | Rithmic's conformance process. Not code. |
| Tradovate | **DECLARED** | OAuth application and partner admission are not self-serve. |
| ProjectX | **DECLARED** | Needs a per-tenant API subscription; gateway host differs per firm. |

`LIVE_EXECUTION_AVAILABLE` is `False` and
`tests/execution/test_boundary.py` asserts the simulator is the only provider
whose `live_connector_implemented` is `True`. Flipping one of the others is the
deliberate act that makes this build able to trade, and that test is where it
gets noticed.

---

## Change review

The boundary Horizon adds, stated as a rule:

**A rule set may be used before it is reviewed. It may never be *reported* as
reviewed.** Refusing to show an account because a review lapsed would be its own
kind of unhelpful; showing lapsed numbers as checked is the failure worth
preventing. So `needs_review` is true for unverified *and* expired, the
catalogue counts both, and the Prop Desk's Rules tab carries the state in the
row rather than in a tooltip.

Three consequences, each tested:

1. **A verification must expire.** `RuleProvenance` refuses `verified=True`
   without `review_expires_at`. Prop firms change contracts without sending
   anybody a diff, so a verification with no expiry is a claim that decays
   silently while continuing to show green.
2. **A verification must name what it was checked against.** `verified=True`
   with neither a source URL nor a hash is refused.
3. **An account keeps the numbers it opened with.** Editing a rule file does not
   move an open account's contract. The account would otherwise be held to terms
   nobody decided to move it to.

### What an assistant may do here

Read the catalogue (`list_rule_sets`, read-only, unprotected). It cannot open an
account against a rule set, and there is deliberately no action for it:
`tests/api/test_prop_rules_api.py::test_no_action_opens_a_prop_account_from_a_rule_file`
holds that apart. Opening an account fixes which numbers a desk holds it to for
the life of the account, and an assistant choosing that from an unreviewed file
would be choosing the contract.

---

## Not built, and named so the absence is visible

- **Payout scheduling and withdrawal tracking.** Payout fields are read from
  rule files and recorded as a note; nothing computes a payout.
- **Multi-firm contract comparison.** Would require asserting what each firm's
  terms say.
- **Automatic rule discovery.** No scraping. A rule set is something a person
  enters and a person checks.
- **Rule-set editing in the interface.** The files are edited on disk. The
  catalogue reads them fresh on every request, so a correction appears
  immediately.
- **Per-firm behavioural quirks.** If two firms need two code paths, the rule
  schema is wrong. That is a bug report, not a feature request.
