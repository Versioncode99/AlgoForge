from forge.oracles import nautilus_capability


def test_nautilus_probe_never_claims_unconfigured_depth() -> None:
    status = nautilus_capability(("BARS",))
    assert status.role == "OPTIONAL_EXECUTION_SEMANTICS_ORACLE"
    assert "L2" in status.supported_data_levels
    if not status.installed:
        assert status.ready is False
        assert status.configured_data_levels == ()
