"""Disclosures: accurate, versioned by their own text, and not legal documents."""

from __future__ import annotations

import importlib

import pytest
from forge.propdesk.consent import (
    DISCLOSURES,
    Acknowledgement,
    ConsentError,
    Disclosure,
    DisclosureKey,
    catalogue,
    current_version,
    is_current,
    require,
)
from forge.propdesk.risk import PROHIBITIONS


def accept(key: DisclosureKey, who: str = "operator", **kwargs) -> Acknowledgement:
    disclosure = DISCLOSURES[key]
    return Acknowledgement(
        key=key,
        version=disclosure.version,
        acknowledged_by=who,
        accepted=disclosure.acknowledgements,
        **kwargs,
    )


class TestTheText:
    def test_no_disclosure_claims_legal_protection(self) -> None:
        # The purpose is informed consent, not indemnity. A disclosure that
        # tells somebody what they have waived is not something this module
        # will carry.
        with pytest.raises(ValueError, match="may not make a legal claim"):
            Disclosure(
                key=DisclosureKey.COPY_TRADING,
                title="x",
                body=("You hold harmless AlgoForge for any losses.",),
                acknowledgements=("ok",),
                confirm_label="Go",
            )

    def test_the_check_catches_the_liability_phrasings_that_matter(self) -> None:
        for phrasing in (
            "AlgoForge is not liable for losses.",
            "You waive any claim against us.",
            "This completely protects AlgoForge from liability.",
            "You assume all risk.",
        ):
            with pytest.raises(ValueError, match="legal claim"):
                Disclosure(
                    key=DisclosureKey.COPY_TRADING,
                    title="x",
                    body=(phrasing,),
                    acknowledgements=("ok",),
                    confirm_label="Go",
                )

    def test_the_ai_risk_disclosure_says_what_it_cannot_do(self) -> None:
        body = " ".join(DISCLOSURES[DisclosureKey.AI_RISK_MANAGEMENT].body)
        assert "cannot override" in body
        assert "remains the hard ceiling" in body
        assert "does not guarantee profits" in body

    def test_the_autonomous_disclosure_names_the_loss_it_can_cause(self) -> None:
        body = " ".join(DISCLOSURES[DisclosureKey.AUTONOMOUS_DEPLOYMENT].body)
        assert "can result in financial losses" in body
        assert "may fail in future market conditions" in body

    def test_the_simulated_execution_disclosure_does_not_imply_money_moves(self) -> None:
        body = " ".join(DISCLOSURES[DisclosureKey.SIMULATED_EXECUTION].body)
        assert "no live broker connector" in body
        assert "no money moves" in body

    def test_the_copy_disclosure_warns_that_followers_place_unseen_orders(self) -> None:
        # Net-position convergence is the reason, and it is a genuine surprise
        # to somebody expecting an order mirror.
        body = " ".join(DISCLOSURES[DisclosureKey.COPY_TRADING].body)
        assert "net position" in body
        assert "you did not see the leader place" in body

    def test_every_disclosure_has_at_least_one_acknowledgement(self) -> None:
        for disclosure in DISCLOSURES.values():
            assert disclosure.acknowledgements
            assert disclosure.body


class TestClaims:
    def test_every_claim_names_a_control_that_exists(self) -> None:
        for disclosure in DISCLOSURES.values():
            for claim in disclosure.claims:
                parts = claim.enforced_by.split(".")
                module = None
                remainder: list[str] = []
                for index in range(len(parts), 0, -1):
                    try:
                        module = importlib.import_module(".".join(parts[:index]))
                        remainder = parts[index:]
                        break
                    except ModuleNotFoundError:
                        continue
                assert module is not None, f"{claim.enforced_by} names nothing importable"
                target = module
                for name in remainder:
                    assert hasattr(target, name), f"{claim.enforced_by} has no {name}"
                    target = getattr(target, name)

    def test_the_ai_disclosures_claims_match_the_prohibition_inventory(self) -> None:
        enforcers = {item.enforced_by for item in PROHIBITIONS}
        claims = {
            claim.enforced_by
            for claim in DISCLOSURES[DisclosureKey.AI_RISK_MANAGEMENT].claims
        }
        unbacked = claims - enforcers
        assert not unbacked, f"claims not backed by a listed prohibition: {unbacked}"


class TestVersioning:
    def test_the_version_is_derived_from_the_text(self) -> None:
        original = DISCLOSURES[DisclosureKey.COPY_TRADING]
        edited = original.model_copy(update={"body": (*original.body, "One more sentence.")})
        assert edited.version != original.version

    def test_changing_only_a_button_label_does_not_invalidate_consent(self) -> None:
        # The label is not what was agreed to.
        original = DISCLOSURES[DisclosureKey.COPY_TRADING]
        relabelled = original.model_copy(update={"confirm_label": "Turn on"})
        assert relabelled.version == original.version

    def test_an_acknowledgement_of_an_older_version_is_not_current(self) -> None:
        stale = Acknowledgement(
            key=DisclosureKey.COPY_TRADING,
            version="0000000000000000",
            acknowledged_by="operator",
            accepted=DISCLOSURES[DisclosureKey.COPY_TRADING].acknowledgements,
        )
        assert not is_current(stale)


class TestAcknowledgement:
    def test_ticking_only_some_boxes_is_refused(self) -> None:
        disclosure = DISCLOSURES[DisclosureKey.AUTONOMOUS_DEPLOYMENT]
        with pytest.raises(ValueError, match="were not accepted"):
            Acknowledgement(
                key=DisclosureKey.AUTONOMOUS_DEPLOYMENT,
                version=disclosure.version,
                acknowledged_by="operator",
                accepted=disclosure.acknowledgements[:1],
            )

    def test_an_anonymous_acknowledgement_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Acknowledgement(
                key=DisclosureKey.COPY_TRADING,
                version=current_version(DisclosureKey.COPY_TRADING),
                acknowledged_by="",
                accepted=DISCLOSURES[DisclosureKey.COPY_TRADING].acknowledgements,
            )

    def test_requiring_an_unacknowledged_disclosure_refuses_with_its_title(self) -> None:
        with pytest.raises(ConsentError, match="AI Risk Management"):
            require(DisclosureKey.AI_RISK_MANAGEMENT, ())

    def test_a_stale_acknowledgement_refuses_differently_from_a_missing_one(self) -> None:
        # "You agreed to an earlier version of this" is actionable; "not
        # acknowledged" sends the operator looking for something they did.
        stale = Acknowledgement(
            key=DisclosureKey.AI_RISK_MANAGEMENT,
            version="deadbeefdeadbeef",
            acknowledged_by="operator",
            accepted=DISCLOSURES[DisclosureKey.AI_RISK_MANAGEMENT].acknowledgements,
        )
        with pytest.raises(ConsentError, match="has changed since it was acknowledged"):
            require(DisclosureKey.AI_RISK_MANAGEMENT, (stale,))

    def test_a_current_acknowledgement_is_returned(self) -> None:
        given = accept(DisclosureKey.AI_RISK_MANAGEMENT)
        assert require(DisclosureKey.AI_RISK_MANAGEMENT, (given,)) == given

    def test_an_account_specific_acknowledgement_does_not_cover_another_account(self) -> None:
        given = accept(DisclosureKey.AI_RISK_MANAGEMENT, account_uid="acct-1")
        assert require(DisclosureKey.AI_RISK_MANAGEMENT, (given,), account_uid="acct-1")
        with pytest.raises(ConsentError):
            require(DisclosureKey.AI_RISK_MANAGEMENT, (given,), account_uid="acct-2")

    def test_an_installation_wide_acknowledgement_covers_every_account(self) -> None:
        given = accept(DisclosureKey.AI_RISK_MANAGEMENT)
        assert require(DisclosureKey.AI_RISK_MANAGEMENT, (given,), account_uid="acct-9")


class TestCatalogue:
    def test_every_disclosure_is_rendered_with_its_current_version(self) -> None:
        rendered = catalogue()
        assert {item["key"] for item in rendered} == {key.value for key in DisclosureKey}
        for item in rendered:
            assert item["version"] == current_version(DisclosureKey(item["key"]))
            assert item["body"] and item["acknowledgements"] and item["confirm_label"]
