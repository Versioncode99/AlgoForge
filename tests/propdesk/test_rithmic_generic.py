"""Rithmic is one adapter, and the rest of the desk has never heard of it.

The failure this is written against is the ordinary one: a provider integration
that starts behind an interface and ends with `if provider == "rithmic"` in the
allocation logic, a `fcm_id` on a copy relationship, and a risk rule that reads
a Rithmic status string. By then the interface is decoration and the second
provider costs as much as the first.

So the check is mechanical: no module outside `forge.propdesk.rithmic` may name
Rithmic's vocabulary, and the adapter's own outputs are the desk's own types.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from forge.propdesk.identity import Account, Provider
from forge.propdesk.orders import OrderEvent, Position
from forge.propdesk.rithmic import normalise

ROOT = Path(__file__).resolve().parents[2]
PROPDESK = ROOT / "packages" / "forge" / "propdesk"

#: Rithmic's own vocabulary. A name from this list outside the adapter package
#: is provider knowledge that has leaked into generic code.
RITHMIC_VOCABULARY = (
    "fcm_id",
    "ib_id",
    "infra_type",
    "template_id",
    "basket_id",
    "ssboe",
    "rq_handler_rp_code",
    "rithmic_ssl_cert_auth_params",
    "total_fill_size",
    "fill_buy_qty",
    "fill_sell_qty",
)

#: Where Rithmic knowledge is allowed to be.
ADAPTER_PACKAGE = "rithmic"

#: The one place provider-specific *names* are allowed outside the adapter.
#:
#: `identity.py` is a registry: `PROVIDERS`, the enum, and one descriptor per
#: provider declaring which fields make up an account key. Rithmic qualifies an
#: account by clearing firm and introducing broker, and a registry that could
#: not say so would force every caller to know it instead — which is the
#: leakage this file exists to prevent, arriving by a different route.
#:
#: What it may hold is a *declaration*. It holds no message shapes, no template
#: ids and no status strings, and the parametrised check below is what proves
#: that rather than trusting it: `template_id`, `basket_id`, `ssboe`,
#: `total_fill_size` and the rest are refused here as everywhere else.
REGISTRY = {"identity.py"}

#: Names that are account-key *declarations* rather than wire vocabulary, and
#: are therefore allowed in the registry.
DECLARABLE = {"fcm_id", "ib_id"}


def _modules() -> list[Path]:
    return [
        path
        for path in sorted(PROPDESK.rglob("*.py"))
        if ADAPTER_PACKAGE not in path.parts
    ]


@pytest.mark.parametrize("term", RITHMIC_VOCABULARY)
def test_no_generic_module_names_rithmics_wire_vocabulary(term: str) -> None:
    allowed = REGISTRY if term in DECLARABLE else set()
    offenders = [
        path.relative_to(PROPDESK).as_posix()
        for path in _modules()
        if term in path.read_text(encoding="utf-8") and path.name not in allowed
    ]
    assert not offenders, (
        f"'{term}' is Rithmic's wire vocabulary and appears in {offenders}. "
        "Provider knowledge belongs in forge.propdesk.rithmic."
    )


def test_the_registry_declares_the_key_fields_and_nothing_about_the_wire() -> None:
    """The exemption above, bounded: a declaration, not a decoder."""
    text = (PROPDESK / "identity.py").read_text(encoding="utf-8")
    for term in set(RITHMIC_VOCABULARY) - DECLARABLE:
        assert term not in text, f"the registry has grown a wire detail: {term}"


def test_the_generic_modules_do_not_import_the_rithmic_package() -> None:
    """One direction only: the adapter knows the desk, not the other way round."""
    offenders: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and ADAPTER_PACKAGE in (node.module or ""):
                offenders.append(path.relative_to(PROPDESK).as_posix())
            if isinstance(node, ast.Import):
                offenders.extend(
                    path.relative_to(PROPDESK).as_posix()
                    for alias in node.names
                    if ADAPTER_PACKAGE in alias.name
                )
    assert not offenders, f"{sorted(set(offenders))} import the Rithmic adapter"


def test_the_allocation_copy_risk_and_policy_layers_never_name_the_provider() -> None:
    """The layers a second provider would otherwise cost the most to add."""
    for name in ("allocation.py", "copy.py", "risk.py", "policy.py", "scaling.py"):
        text = (PROPDESK / name).read_text(encoding="utf-8").lower()
        # A comment naming Rithmic as an example is fine; a code path is not.
        code = "\n".join(
            line for line in text.splitlines()
            if not line.strip().startswith("#") and not line.strip().startswith("*")
        )
        assert '"rithmic"' not in code and "'rithmic'" not in code, (
            f"{name} branches on the provider by name"
        )


# ── what comes out of the adapter is the desk's own shapes ───────────────────


class Row:
    def __init__(self, **fields: object) -> None:
        for key, value in fields.items():
            setattr(self, key, value)


def test_a_normalised_account_is_the_desks_account_and_nothing_more() -> None:
    account = normalise.account(
        Row(account_id="A1", account_name="One", fcm_id="F", ib_id="I"),
        connection_id="c1",
        credential_ref="cred",
        environment=__import__(
            "forge.propdesk.identity", fromlist=["Environment"]
        ).Environment.DEMO,
    )
    assert isinstance(account, Account)
    assert account.key.provider is Provider.RITHMIC
    # The provider's qualifiers are inside the identity, where a registry keeps
    # them, rather than spread across the record.
    assert set(account.key.qualifiers) == {"fcm_id", "ib_id"}


def test_a_normalised_event_is_the_desks_order_event() -> None:
    _, event = normalise.order_event(Row(basket_id="B1", status="open"))
    assert isinstance(event, OrderEvent)
    assert "rithmic" not in event.model_dump_json().lower()


def test_a_normalised_position_is_the_desks_position() -> None:
    row = normalise.position(Row(symbol="NQZ6", fill_buy_qty=3, fill_sell_qty=1), account_uid="A1")
    assert isinstance(row, Position)
    assert row.quantity == 2
    assert "rithmic" not in row.model_dump_json().lower()
