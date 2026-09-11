"""The layer model, asserted rather than assumed.

These tests exist because the single most expensive confusion in this domain is
treating a trading platform as a place orders go. Every one of them fails loudly
if somebody adds a front end to the provider list, or marks a connector
implemented that is not.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge.propdesk import (
    PLATFORM_BINDINGS,
    PROVIDER_DESCRIPTORS,
    AccountKey,
    DataFeed,
    Environment,
    ExecutionVenue,
    Platform,
    Provider,
    VenueKind,
    binding,
    catalogue,
    descriptor,
    provider_for_platform,
)
from forge.propdesk.adapters import (
    SimulatedAdapter,
    projectx_adapter,
    rithmic_adapter,
    tradovate_adapter,
)
from pydantic import ValidationError

NOW = datetime(2026, 9, 11, tzinfo=UTC)


def test_ninjatrader_desktop_is_a_platform_and_not_a_provider() -> None:
    """The headline finding of the research, as a test.

    NinjaTrader Desktop holds only local sim accounts. Everything else it shows
    belongs to whichever provider it is connected to, and its only external
    control surfaces are a local file interface and in-platform add-ons —
    neither of which is a provider API.
    """
    assert "ninjatrader" not in {p.value for p in Provider}
    assert Platform.NINJATRADER_DESKTOP in PLATFORM_BINDINGS
    assert provider_for_platform(Platform.NINJATRADER_DESKTOP) is None
    note = binding(Platform.NINJATRADER_DESKTOP).note
    assert "Tradovate" in note and "platform" in note.lower()


def test_ninjatrader_brokerage_accounts_reach_the_tradovate_provider() -> None:
    """The other half: the brokerage side is Tradovate, and the model says so."""
    tradovate = descriptor(Provider.TRADOVATE)
    assert "NinjaTrader" in tradovate.display_name
    assert "platform" in tradovate.what_it_is.lower()


def test_tradesea_and_topstepx_are_aliases_rather_than_providers() -> None:
    assert provider_for_platform(Platform.TRADESEA) is Provider.RITHMIC
    assert provider_for_platform(Platform.TOPSTEPX_WEB) is Provider.PROJECTX
    assert "topstepx" not in {p.value for p in Provider}
    assert "tradesea" not in {p.value for p in Provider}


def test_tradingview_is_only_a_signal_source() -> None:
    tradingview = binding(Platform.TRADINGVIEW)
    assert tradingview.signal_source_only is True
    assert tradingview.provider is None


def test_a_data_feed_can_never_be_configured_to_route_orders() -> None:
    """`routes_orders` is a `Literal[False]`, not a setting somebody can flip."""
    with pytest.raises(ValidationError):
        DataFeed(feed_id="x", name="X", routes_orders=True)  # type: ignore[arg-type]


def test_only_an_exchange_matches_against_the_public_book() -> None:
    with pytest.raises(ValidationError, match="cannot match against the public book"):
        ExecutionVenue(
            venue_id="sim",
            name="A simulator",
            kind=VenueKind.PROVIDER_SIMULATOR,
            matches_public_book=True,
        )


def test_only_the_simulator_claims_an_implemented_live_connector() -> None:
    """The single field that would have to change for this build to trade."""
    implemented = {
        provider
        for provider, item in PROVIDER_DESCRIPTORS.items()
        if item.live_connector_implemented
    }
    assert implemented == {Provider.SIMULATED}


def test_every_real_provider_declares_its_authentication_and_key_fields() -> None:
    for provider in (Provider.RITHMIC, Provider.TRADOVATE, Provider.PROJECTX):
        item = descriptor(provider)
        assert item.account_key_fields, f"{provider} declares no account key fields"
        assert item.limitations, f"{provider} declares no limitations"
        assert item.evidence, f"{provider} cites no evidence"


def test_rithmic_needs_more_than_an_account_id_to_name_an_account() -> None:
    """One credential fans out to many accounts, keyed differently per provider."""
    assert set(descriptor(Provider.RITHMIC).account_key_fields) >= {
        "system",
        "fcm_id",
        "ib_id",
        "account_id",
    }
    assert descriptor(Provider.PROJECTX).account_key_fields == ("tenant", "account_id")


def test_an_account_key_excludes_the_display_name() -> None:
    """A rename must not change an account's identity.

    One commercial product keyed copy configuration by account name and shipped
    a release note saying renames no longer silently delete it.
    """
    assert "display_name" not in AccountKey.model_fields
    assert "name" not in AccountKey.model_fields


def test_two_accounts_with_one_provider_id_under_different_credentials_differ() -> None:
    first = AccountKey(
        provider=Provider.TRADOVATE,
        environment=Environment.DEMO,
        credential_ref="cred-a",
        account_id="12345",
    )
    second = first.model_copy(update={"credential_ref": "cred-b"})
    assert first.key_id != second.key_id
    assert first.canonical != second.canonical


def test_qualifiers_are_part_of_the_identity() -> None:
    plain = AccountKey(
        provider=Provider.PROJECTX,
        environment=Environment.DEMO,
        credential_ref="cred",
        account_id="7",
    )
    tenanted = plain.model_copy(update={"qualifiers": {"tenant": "firm-a"}})
    assert plain.key_id != tenanted.key_id
    assert "tenant=firm-a" in tenanted.canonical


def test_the_catalogue_reports_which_connectors_actually_exist() -> None:
    published = catalogue()
    assert published["live_connectors_implemented"] == ["simulated"]
    assert len(published["providers"]) == len(Provider)
    assert len(published["platforms"]) == len(PLATFORM_BINDINGS)


def test_a_declared_adapter_refuses_to_serve_an_implemented_provider() -> None:
    """The guard that stops a live provider being quietly served by a refusal."""
    from forge.propdesk.adapters.declared import DeclaredAdapter, RequiredWork
    from forge.propdesk.identity import SIMULATED_DESCRIPTOR

    with pytest.raises(ValueError, match="declares a live connector"):
        DeclaredAdapter(SIMULATED_DESCRIPTOR, RequiredWork())


@pytest.mark.parametrize(
    "factory", [rithmic_adapter, tradovate_adapter, projectx_adapter]
)
def test_a_declared_adapter_is_not_a_simulator_either(factory) -> None:
    """Refusing everything is a third category, not a kind of simulation."""
    adapter = factory()
    assert adapter.simulated is False
    assert adapter.descriptor.live_connector_implemented is False


def test_the_simulated_adapter_says_it_is_simulated() -> None:
    assert SimulatedAdapter().simulated is True
