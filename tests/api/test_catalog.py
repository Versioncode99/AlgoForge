"""Families and templates, created at run time on the same terms as shipped ones.

The console assistant used to say the families were fixed and the templates
could not be extended, and it was right. These tests pin the replacement without
losing the property that made the old answer honest: adding a *name* must never
be mistaken for adding a *capability*, and code an agent wrote is guarded and
executed before it is trusted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forge.strategy import FamilyRegistry, TemplateRejected, TemplateStore
from forge.strategy.templates import Template
from forge_api.catalog import EXAMPLE_SOURCE

GOOD_PARAMS = [
    {"name": "lookback", "default": 30, "low": 10, "high": 90, "step": 10, "description": "n"},
    {
        "name": "entry_sigma",
        "default": 2.0,
        "low": 1.0,
        "high": 4.0,
        "step": 0.5,
        "description": "s",
    },
    {
        "name": "compression",
        "default": 0.8,
        "low": 0.4,
        "high": 1.0,
        "step": 0.1,
        "description": "c",
    },
    {"name": "stop_atr", "default": 2.0, "low": 0.5, "high": 4.0, "step": 0.5, "description": "a"},
    {"name": "max_bars", "default": 40, "low": 10, "high": 120, "step": 10, "description": "m"},
]


@pytest.fixture
def families(tmp_path: Path) -> FamilyRegistry:
    return FamilyRegistry(tmp_path / "families")


@pytest.fixture
def store(tmp_path: Path) -> TemplateStore:
    return TemplateStore(tmp_path / "templates")


def create(store: TemplateStore, families: FamilyRegistry, **overrides) -> Template:
    payload = {
        "key": "range_compression_reversal",
        "name": "Range Compression Reversal",
        "family": "mean_reversion",
        "hypothesis": "A" * 60,
        "falsifiable_prediction": "B" * 40,
        "parameters": GOOD_PARAMS,
        "source": EXAMPLE_SOURCE,
        "warmup_bars": 200,
        "known_families": families.runnable_keys(),
        # Declared explicitly: borrowing the process-wide catalogue would make
        # these tests depend on whether another one built an app first.
        "existing_templates": {"momentum_breakout", "mean_reversion_band"},
    }
    return store.create(**{**payload, **overrides})


# ── families ─────────────────────────────────────────────────────────────────
def test_a_family_needs_a_mechanism_not_just_a_name(families: FamilyRegistry):
    with pytest.raises(ValueError, match="mechanism"):
        families.create(key="vibes", label="Vibes", description="", mechanism="too short")


def test_a_family_whose_data_is_missing_is_registered_blocked_not_runnable(
    families: FamilyRegistry,
):
    family = families.create(
        key="dealer_gamma",
        label="Dealer gamma",
        description="",
        mechanism="M" * 60,
        data_requirements=("OPTIONS_CHAIN", "OPEN_INTEREST"),
    )
    # This is the honesty property: naming the family did not conjure the chain.
    assert family.runnable is False
    assert set(family.blocked_by) == {"OPTIONS_CHAIN", "OPEN_INTEREST"}
    assert family.key not in families.runnable_keys()
    assert family.key in families.all_keys()


def test_custom_families_survive_a_restart_and_builtins_cannot_be_deleted(tmp_path: Path):
    path = tmp_path / "families"
    first = FamilyRegistry(path)
    first.create(key="order_flow", label="Order flow", description="", mechanism="M" * 60)
    assert "order_flow" in FamilyRegistry(path).all_keys()
    with pytest.raises(ValueError, match="Built-in"):
        first.delete("momentum")
    first.delete("order_flow")
    assert "order_flow" not in FamilyRegistry(path).all_keys()


# ── templates ────────────────────────────────────────────────────────────────
def test_a_registered_template_runs_before_it_is_accepted(
    store: TemplateStore, families: FamilyRegistry
):
    template = create(store, families)
    report = store.metadata(template.key)["smoke_test"]
    # "It parsed" is not the same as "it runs". The smoke test executes the
    # module over synthetic bars, which is what stops an agent's template
    # crashing eight engine workers at once.
    assert report["ok"] is True
    assert report["lookahead_clean"] is True
    assert (store.path / f"{template.key}.py").exists()


def test_the_guard_refuses_a_template_that_reaches_outside_the_runtime(
    store: TemplateStore, families: FamilyRegistry
):
    hostile = (
        "import os\n"
        "def entry_signal(w, p):\n"
        "    open('secrets.txt')\n"
        "    return 1\n"
        "def exit_signal(w, p, pos):\n"
        "    return None\n"
    )
    with pytest.raises(TemplateRejected, match="static guard"):
        create(store, families, key="hostile", source=hostile)
    assert not (store.path / "hostile.json").exists()


def test_a_template_that_raises_at_run_time_never_enters_the_catalogue(
    store: TemplateStore, families: FamilyRegistry
):
    broken = (
        "def entry_signal(w, p):\n"
        "    return w.closes[10 ** 9]\n"
        "def exit_signal(w, p, pos):\n"
        "    return None\n"
    )
    with pytest.raises(TemplateRejected, match="smoke test"):
        create(store, families, key="broken", source=broken)
    assert store.keys() == []


def test_a_template_is_refused_for_an_unknown_family(
    store: TemplateStore, families: FamilyRegistry
):
    with pytest.raises(TemplateRejected, match="Unknown family"):
        create(store, families, family="not_a_family")


@pytest.mark.parametrize(
    ("parameters", "match"),
    [
        ([], "at least one parameter"),
        ([{"name": "a", "default": 1, "low": 5, "high": 1, "step": 1}], "low must be below high"),
        ([{"name": "a", "default": 9, "low": 1, "high": 5, "step": 1}], "outside its range"),
        ([{"name": "a", "default": 1, "low": 1, "high": 2, "step": 5}], "not tunable"),
        ([{"name": "a", "default": 1, "low": 0, "high": 100000, "step": 1}], "multiple-testing"),
    ],
)
def test_parameter_grids_that_cannot_be_searched_are_refused(
    store: TemplateStore, families: FamilyRegistry, parameters: list, match: str
):
    with pytest.raises(TemplateRejected, match=match):
        create(store, families, key="grid_probe", parameters=parameters)


def test_a_stored_template_reloads_into_the_shared_catalogue(
    tmp_path: Path, families: FamilyRegistry
):
    path = tmp_path / "templates"
    create(TemplateStore(path), families)
    catalogue: dict[str, Template] = {}
    restored = TemplateStore(path).load_all(catalogue)
    assert [t.key for t in restored] == ["range_compression_reversal"]
    # The engine cannot tell a restored template from a shipped one, which is the
    # point: it is registered into the same dict every consumer already holds.
    assert catalogue["range_compression_reversal"].family == "mean_reversion"


def test_a_stored_template_that_stops_passing_the_guard_is_dropped_not_fatal(
    tmp_path: Path, families: FamilyRegistry
):
    path = tmp_path / "templates"
    template = create(TemplateStore(path), families)
    (path / f"{template.key}.py").write_text("import socket\n", encoding="utf-8")

    catalogue: dict[str, Template] = {}
    store = TemplateStore(path)
    assert store.load_all(catalogue) == []
    assert catalogue == {}
    # A hand-edited file must not stop the application starting; it is reported.
    assert store.rejected_on_load[0]["key"] == template.key
