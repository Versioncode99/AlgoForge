from forge.capabilities import nautilus_capability


def test_nautilus_probe_never_claims_unconfigured_depth() -> None:
    status = nautilus_capability(("BARS",))
    assert status.role == "INSTALLED_PACKAGE_PROBE"
    assert "L2" in status.supported_data_levels
    if not status.installed:
        assert status.ready is False
        assert status.configured_data_levels == ()


def test_the_probe_does_not_present_itself_as_an_evaluator() -> None:
    """It reports what is installed. Calling it an oracle invited the reading
    that something independently checks execution realism. Nothing does."""
    status = nautilus_capability(("BARS",))
    assert status.capability_id == "nautilus-trader"
    assert "ORACLE" not in status.role
    assert any("cannot promote" in note for note in status.limitations)
