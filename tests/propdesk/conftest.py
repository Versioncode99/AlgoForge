"""Fixtures the Prop Desk tests share.

All of them build a *simulated* desk. There is no live broker in this build and
these tests do not pretend otherwise: the only adapter that executes is the
local simulator, and the tests that exercise a real provider assert that it
refuses.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge.execution.gate import InstrumentRule, MarketState
from forge.prop.account import AccountRules, AccountState, TrailMode
from forge.propdesk import (
    Account,
    AccountKey,
    AuthMethod,
    BrokerConnection,
    ConnectionState,
    CopyGroup,
    CredentialRecord,
    DeskAccount,
    DeskContext,
    Environment,
    ExecutionFabric,
    Follower,
    Leader,
    Permission,
    PropDesk,
    PropProgramPolicy,
    Provider,
    SizingPolicy,
    UseCase,
    default_catalogue,
    evaluate_compatibility,
)
from forge.propdesk.adapters import SimulatedAccount, SimulatedAdapter

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def catalogue():
    return default_catalogue()


@pytest.fixture
def credential() -> CredentialRecord:
    return CredentialRecord(
        credential_ref="sim-credential",
        provider=Provider.SIMULATED,
        auth_method=AuthMethod.NONE,
        label="Simulator",
        created_at=NOW,
    )


@pytest.fixture
def adapter() -> SimulatedAdapter:
    """One simulated connection holding a leader and two followers."""
    sim = SimulatedAdapter(connection_id="conn-sim", now=lambda: NOW)
    sim.add_account(SimulatedAccount("LEADER", display_name="Leader", balance=150_000.0))
    sim.add_account(SimulatedAccount("F1", display_name="Follower one", balance=50_000.0))
    sim.add_account(
        SimulatedAccount(
            "F2", display_name="Follower two", balance=50_000.0, max_contracts=2
        )
    )
    sim.mark("MNQ", 20_000.0)
    sim.mark("NQ", 20_000.0)
    return sim


@pytest.fixture
def connection() -> BrokerConnection:
    return BrokerConnection(
        connection_id="conn-sim",
        provider=Provider.SIMULATED,
        environment=Environment.SIMULATION,
        label="Simulator",
        credential_ref="sim-credential",
        state=ConnectionState.LIVE,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def fabric(connection, adapter, credential) -> ExecutionFabric:
    fabric = ExecutionFabric(now=lambda: NOW)
    fabric.register(connection, adapter)
    fabric.connect(connection.connection_id, credential)
    fabric.discover_accounts(connection.connection_id)
    return fabric


@pytest.fixture
def desk(fabric) -> PropDesk:
    return PropDesk(fabric, now=lambda: NOW)


@pytest.fixture
def permissive_policy() -> PropProgramPolicy:
    """A policy with every relevant permission recorded as allowed.

    Deliberately explicit: nothing in the product ships a policy like this, and
    a test that needs one has to build it, which is the same friction the
    operator has.
    """
    return PropProgramPolicy(
        firm_label="test firm",
        program_label="test programme",
        automation=Permission.ALLOWED,
        copy_in=Permission.ALLOWED,
        copy_out=Permission.ALLOWED,
        algorithmic_allocation=Permission.ALLOWED,
        permitted_products=("MNQ", "NQ", "MES", "ES"),
        source_note="fixture; not a claim about any real firm",
    )


@pytest.fixture
def rules() -> AccountRules:
    return AccountRules(
        name="Test 50k",
        starting_balance=50_000.0,
        maximum_loss=2_000.0,
        trail_mode=TrailMode.END_OF_DAY,
        daily_loss_limit=1_000.0,
        profit_target=3_000.0,
        max_position_contracts=10,
    )


@pytest.fixture
def account_state() -> AccountState:
    return AccountState(
        as_of=NOW,
        balance=50_000.0,
        equity=50_000.0,
        high_water_balance=50_000.0,
        high_water_equity=50_000.0,
    )


def desk_account(
    account_uid: str,
    *,
    rules: AccountRules,
    state: AccountState,
    policy: PropProgramPolicy | None,
    use_case: UseCase = UseCase.COPY_FOLLOWER,
    positions: dict[str, int] | None = None,
    connection_state: ConnectionState = ConnectionState.LIVE,
) -> DeskAccount:
    report = (
        None
        if policy is None
        else evaluate_compatibility(
            account_uid=account_uid,
            use_case=use_case,
            policy=policy,
            at=NOW,
        )
    )
    return DeskAccount(
        account_uid=account_uid,
        display_name=account_uid,
        connection_state=connection_state,
        rules=rules,
        state=state,
        compatibility=report,
        positions=positions or {},
    )


@pytest.fixture
def context_factory(rules, account_state, permissive_policy, adapter):
    """Build a `DeskContext` for a set of accounts, with sane market state."""

    def build(
        account_uids: tuple[str, ...] = ("F1", "F2"),
        *,
        policy: PropProgramPolicy | None = None,
        use_case: UseCase = UseCase.COPY_FOLLOWER,
        positions: dict[str, dict[str, int]] | None = None,
        news=None,
        connection_state: ConnectionState = ConnectionState.LIVE,
    ) -> DeskContext:
        chosen = permissive_policy if policy is None else policy
        books = positions or {}
        accounts = {
            adapter.account_uid(uid): desk_account(
                adapter.account_uid(uid),
                rules=rules,
                state=account_state,
                policy=chosen,
                use_case=use_case,
                positions=books.get(uid, {}),
                connection_state=connection_state,
            )
            for uid in account_uids
        }
        return DeskContext(
            now=NOW,
            accounts=accounts,
            owner_positions={
                adapter.account_uid(uid): dict(books.get(uid, {}))
                for uid in account_uids
            },
            instruments={
                "MNQ": InstrumentRule(symbol="MNQ", tradable=True, multiplier=2.0),
                "NQ": InstrumentRule(symbol="NQ", tradable=True, multiplier=20.0),
            },
            market={
                "MNQ": MarketState(
                    symbol="MNQ", open=True, last_price=20_000.0, last_price_at=NOW
                ),
                "NQ": MarketState(
                    symbol="NQ", open=True, last_price=20_000.0, last_price_at=NOW
                ),
            },
            use_case=use_case,
            product_groups={"MNQ": "NASDAQ100", "NQ": "NASDAQ100"},
            news=news,
            # Deliberately no capital figure: see the note in
            # `forge.propdesk.desk` on why a notional-weight limit refuses every
            # order a leveraged futures account has ever placed.
        )

    return build


@pytest.fixture
def group(adapter) -> CopyGroup:
    return CopyGroup(
        group_id="grp-1",
        name="Test group",
        leader=Leader(account_uid=adapter.account_uid("LEADER")),
        followers=(
            Follower(
                account_uid=adapter.account_uid("F1"),
                sizing=SizingPolicy(value=1.0),
            ),
            Follower(
                account_uid=adapter.account_uid("F2"),
                sizing=SizingPolicy(value=1.0),
            ),
        ),
        active=True,
        owner_attested_by="tester",
        created_at=NOW,
        updated_at=NOW,
    )


def key(account_id: str) -> AccountKey:
    return AccountKey(
        provider=Provider.SIMULATED,
        environment=Environment.SIMULATION,
        credential_ref="sim-credential",
        account_id=account_id,
    )


def account(account_id: str) -> Account:
    return Account(
        key=key(account_id), connection_id="conn-sim", display_name=account_id
    )
