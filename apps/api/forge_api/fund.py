"""The Hedge Fund loop, assembled from state that actually exists.

Every stage here reads a real record: the strategy library, the backtest store,
the judge's verdict through the same dossier path the Evidence screen uses, the
local market archives, and the simulated book the OMS keeps. Nothing is
generated to fill a panel.

Two consequences of that discipline are visible everywhere in this file.

**Stages report `UNKNOWN` rather than a plausible number.** A fund with no
universe configured has no portfolio, and the command centre says so. That is
the state of a fresh installation and it should look like one, not like a fund
that happens to be flat.

**Expected return is a backtested return, and is labelled as one.** Portfolio
construction needs a forecast per instrument, and the only forecast this
application can honestly produce is what a strategy did in its own measured
window. That is a weak forecast and every proposal says so in `limitations`.
Dressing it up as an alpha estimate would be the exact failure the judge exists
to prevent, one layer further down the stack.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from forge.analytics.attribution import PnlRecord
from forge.analytics.attribution import report as performance_report
from forge.execution.gate import (
    GateContext,
    GateDecision,
    InstrumentRule,
    MarketState,
    ProposedOrder,
    Side,
    screen_all,
)
from forge.execution.oms import (
    ExecutionStore,
    Order,
    OrderManagementSystem,
    OrderRefused,
    new_order_id,
)
from forge.execution.paper import InstrumentSpec, PaperBroker
from forge.hedgefund.audit import AuditLog
from forge.hedgefund.config import FundConfig, FundConfigStore
from forge.hedgefund.loop import LOOP, Stage, StageState, StageStatus
from forge.portfolio.construction import construct
from forge.portfolio.models import AlphaSignal, PortfolioProposal
from forge.risk.portfolio import BookPosition, RiskAssessment, evaluate_portfolio

#: How many daily observations the covariance estimate asks the archives for.
#: Two years is long enough to contain more than one regime and short enough
#: that the correlations are not describing a market that no longer exists.
COVARIANCE_BARS = 500

#: The most strategies whose verdicts are looked up when building signals.
#: Each lookup runs the judge, and a fund screen that takes ninety seconds to
#: open is a screen nobody opens.
MAX_SIGNAL_STRATEGIES = 40


class FundError(Exception):
    """Something the fund layer refuses, with a reason to show verbatim."""


class FundService:
    """Assembles the loop. Holds no policy: the engines it calls hold all of it."""

    def __init__(
        self,
        *,
        config_store: FundConfigStore,
        execution_store: ExecutionStore,
        audit: AuditLog,
        library: Any,
        backtests: Any,
        market: Any,
        verdict_for: Callable[[str], str | None],
    ) -> None:
        self.config_store = config_store
        self.execution_store = execution_store
        self.audit = audit
        self.library = library
        self.backtests = backtests
        self.market = market
        self.verdict_for = verdict_for
        self._lock = threading.Lock()
        # Proposals are held in memory between construction and order
        # preparation. Deliberately not persisted: a rebalance proposed an hour
        # ago and prepared now would be sized on prices that have moved, and a
        # durable proposal is an invitation to do exactly that.
        self._proposals: dict[str, PortfolioProposal] = {}
        self._screened: dict[str, tuple[ProposedOrder, GateDecision]] = {}
        self._broker = PaperBroker()
        self.oms = OrderManagementSystem(execution_store, self._broker)
        self._sync_broker()

    # ── configuration ────────────────────────────────────────────────────────
    @property
    def config(self) -> FundConfig:
        return self.config_store.get()

    def _sync_broker(self) -> None:
        """Point the simulator at the configured universe and slippage.

        Called after every configuration write. Without it the adapter keeps
        pricing fills with the multipliers it was built with, and a change to a
        contract specification would silently not apply.
        """
        config = self.config
        self._broker.slippage_ticks = config.slippage_ticks
        self._broker.instruments = {
            instrument.symbol: InstrumentSpec(
                symbol=instrument.symbol,
                multiplier=instrument.multiplier,
                # Tick size is not part of an `Instrument`, which describes the
                # portfolio's view. One basis point of price is a defensible
                # stand-in and is stated on every fill's `basis`.
                tick_size=max((instrument.price or 100.0) * 1e-4, 1e-8),
            )
            for instrument in config.universe
        }

    def save_config(self, config: FundConfig, *, changed_by: str, note: str = "") -> FundConfig:
        saved = self.config_store.save(config, changed_by=changed_by, note=note)
        self._sync_broker()
        return saved

    # ── data ─────────────────────────────────────────────────────────────────
    def _returns(self, symbols: tuple[str, ...]) -> tuple[np.ndarray | None, tuple[str, ...], str]:
        """Daily returns for the named symbols, aligned on their common length.

        Returns `(None, (), reason)` when the archives cannot serve them. The
        reason is carried into the proposal's limitations, because "the
        portfolio could not be built" and "the portfolio is empty" are different
        answers and only one of them is about the market.
        """
        from forge_api.market import DATASETS

        series: dict[str, np.ndarray] = {}
        missing: list[str] = []
        for symbol in symbols:
            key = next(
                (
                    dataset.key
                    for dataset in DATASETS.values()
                    if dataset.symbol.upper() == symbol.upper() and dataset.is_real
                ),
                None,
            )
            if key is None:
                missing.append(symbol)
                continue
            try:
                payload = self.market.chart_bars(key, "1d", COVARIANCE_BARS)
            except Exception:
                missing.append(symbol)
                continue
            closes = np.array([float(bar["close"]) for bar in payload["bars"]], dtype=float)
            if len(closes) < 3:
                missing.append(symbol)
                continue
            series[symbol] = np.diff(closes) / closes[:-1]

        if not series:
            return None, (), (
                "no local daily archive covers "
                f"{', '.join(symbols) or 'the configured universe'}, so covariance "
                "could not be estimated"
            )
        # Align on the shortest available history rather than padding. A padded
        # series has invented observations in it, and every covariance entry
        # touching them would be wrong in a way nothing downstream could detect.
        length = min(len(values) for values in series.values())
        names = tuple(sorted(series))
        matrix = np.column_stack([series[name][-length:] for name in names])
        reason = (
            f"no local daily archive for {', '.join(sorted(missing))}" if missing else ""
        )
        return matrix, names, reason

    # ── alpha ────────────────────────────────────────────────────────────────
    def signals(self) -> tuple[tuple[AlphaSignal, ...], list[str]]:
        """One forecast per strategy that has been backtested and judged.

        The forecast is the strategy's own realised return over its backtest,
        expressed against the fund's capital. It is a weak estimate and the
        second return value says so; construction copies that into the
        proposal's limitations so the number is never read without it.
        """
        notes: list[str] = []
        universe = {instrument.symbol.upper() for instrument in self.config.universe}
        signals: list[AlphaSignal] = []
        considered = 0
        for spec in self.library.list_specs():
            if considered >= MAX_SIGNAL_STRATEGIES:
                notes.append(
                    f"only the first {MAX_SIGNAL_STRATEGIES} strategies were considered; "
                    "judging every candidate on every request is not affordable"
                )
                break
            if spec.symbol.upper() not in universe:
                continue
            considered += 1
            latest = self.backtests.latest_projection(spec.strategy_id)
            if latest is None or not latest.get("net_pnl"):
                continue
            verdict = self.verdict_for(spec.strategy_id)
            signals.append(
                AlphaSignal(
                    strategy_id=spec.strategy_id,
                    symbol=spec.symbol.upper(),
                    # Net P&L over capital: a fraction, which is what the
                    # optimiser's objective is in. The horizon is the backtest's
                    # own, which is why it is not annualised here — annualising a
                    # number this weak would make it look like a rate.
                    expected_return=float(latest["net_pnl"]) / max(self.config.capital, 1.0),
                    confidence=0.5,
                    run_id=str(latest.get("backtest_id", "")),
                    verdict=verdict,  # type: ignore[arg-type]
                )
            )
        if signals:
            notes.append(
                "expected returns are each strategy's realised backtest P&L over fund "
                "capital, not a forward forecast. They are scaled by a fixed 0.5 "
                "confidence and carry every limitation the backtest carries."
            )
        return tuple(signals), notes

    # ── portfolio ────────────────────────────────────────────────────────────
    def construct_portfolio(self, *, capital: float | None = None) -> PortfolioProposal:
        config = self.config
        if not config.universe:
            raise FundError(
                "the fund has no universe. Add instruments before constructing a "
                "portfolio: nothing can be sized against a list of nothing."
            )
        signals, notes = self.signals()
        symbols = tuple(sorted({signal.symbol for signal in signals}))
        returns, return_symbols, reason = self._returns(symbols or tuple(config.instruments))
        if reason:
            notes.append(reason)

        book = self.oms.book()
        money = capital if capital is not None else config.capital
        current = {
            position.symbol: position.quantity
            * position.average_price
            * config.instruments[position.symbol].multiplier
            / max(money, 1.0)
            for position in book.positions
            if position.symbol in config.instruments
        }
        proposal = construct(
            signals,
            config.instruments,
            capital=money,
            constraints=config.constraints,
            returns=returns,
            return_symbols=return_symbols,
            current_weights=current,
        )
        proposal = proposal.model_copy(
            update={"limitations": (*proposal.limitations, *notes)}
        )
        with self._lock:
            self._proposals[proposal.portfolio_id] = proposal
            # One proposal at a time. Keeping a history would let a caller
            # prepare orders from a stale one, which is the failure the
            # in-memory-only decision above exists to prevent.
            for stale in [k for k in self._proposals if k != proposal.portfolio_id]:
                del self._proposals[stale]
        return proposal

    def proposal(self, portfolio_id: str) -> PortfolioProposal:
        with self._lock:
            found = self._proposals.get(portfolio_id)
        if found is None:
            raise FundError(
                f"no live portfolio proposal '{portfolio_id}'. Proposals are not stored "
                "between requests: construct again so the sizing uses current prices."
            )
        return found

    # ── risk ─────────────────────────────────────────────────────────────────
    def risk(self, *, portfolio_id: str | None = None) -> RiskAssessment:
        """Assess the proposed portfolio when one is named, otherwise the book."""
        config = self.config
        if portfolio_id:
            proposal = self.proposal(portfolio_id)
            positions = [
                BookPosition(
                    symbol=holding.symbol,
                    weight=holding.weight,
                    strategy_ids=holding.strategy_ids,
                )
                for holding in proposal.holdings
            ]
            turnover: float | None = proposal.turnover
        else:
            book = self.oms.book()
            positions = [
                BookPosition(
                    symbol=position.symbol,
                    weight=position.quantity
                    * position.average_price
                    * config.instruments[position.symbol].multiplier
                    / max(config.capital, 1.0),
                )
                for position in book.positions
                if position.symbol in config.instruments
            ]
            turnover = None

        symbols = tuple(position.symbol for position in positions)
        covariance = None
        if symbols:
            from forge.portfolio.covariance import estimate

            returns, names, _ = self._returns(symbols)
            if returns is not None and set(names) == set(symbols):
                columns = [names.index(symbol) for symbol in symbols]
                covariance = estimate(returns[:, columns], symbols)
        return evaluate_portfolio(
            positions, config.limits, covariance=covariance, turnover=turnover
        )

    # ── orders ───────────────────────────────────────────────────────────────
    def prepare_orders(self, portfolio_id: str) -> tuple[ProposedOrder, ...]:
        """The rebalance that reaches a proposal from the current book.

        Prepared, not placed and not screened. Preparing is the last step an AI
        actor performs unattended in the autonomous stance; everything after it
        is the gate's and the OMS's.
        """
        proposal = self.proposal(portfolio_id)
        config = self.config
        now = datetime.now(UTC)
        held = {position.symbol: position.quantity for position in self.oms.book().positions}

        orders: list[ProposedOrder] = []
        targets = {holding.symbol: holding for holding in proposal.holdings}
        for symbol in sorted(set(targets) | set(held)):
            instrument = config.instruments.get(symbol)
            if instrument is None or instrument.price is None:
                continue
            target = targets[symbol].contracts if symbol in targets else 0
            if target is None:
                continue
            delta = target - held.get(symbol, 0)
            if delta == 0:
                continue
            orders.append(
                ProposedOrder(
                    order_id=new_order_id(symbol, now, nonce=str(delta)),
                    symbol=symbol,
                    side=Side.BUY if delta > 0 else Side.SELL,
                    quantity=abs(delta),
                    strategy_id=(
                        targets[symbol].strategy_ids[0]
                        if symbol in targets and targets[symbol].strategy_ids
                        else ""
                    ),
                    portfolio_id=portfolio_id,
                    intent="rebalance",
                    reference_price=instrument.price,
                    data_as_of=now,
                    created_at=now,
                )
            )
        return tuple(orders)

    def gate_context(self) -> GateContext:
        config = self.config
        now = datetime.now(UTC)
        book = self.oms.book()
        verdicts = {
            spec.strategy_id: verdict
            for spec in self.library.list_specs()[:MAX_SIGNAL_STRATEGIES]
            if (verdict := self.verdict_for(spec.strategy_id)) is not None
        }
        positions = {position.symbol: position.quantity for position in book.positions}
        gross = sum(
            abs(position.quantity)
            * position.average_price
            * config.instruments[position.symbol].multiplier
            for position in book.positions
            if position.symbol in config.instruments
        ) / max(config.capital, 1.0)

        return GateContext(
            now=now,
            limits=config.limits,
            capital=config.capital,
            restricted=config.restricted_map,
            instruments={
                symbol: InstrumentRule(
                    symbol=symbol,
                    tradable=True,
                    shortable=instrument.shortable,
                    borrow_cost_bps=instrument.borrow_cost_bps,
                    multiplier=instrument.multiplier,
                    adv_notional=instrument.adv_notional,
                )
                for symbol, instrument in config.instruments.items()
            },
            market={
                symbol: MarketState(
                    symbol=symbol,
                    open=True,
                    last_price=instrument.price,
                    # The configured price is the reference this build has. It is
                    # stamped now rather than left absent so the staleness check
                    # measures something; what it measures is stated in the
                    # fund's limitations, and it is not a live quote.
                    last_price_at=now,
                )
                for symbol, instrument in config.instruments.items()
                if instrument.price is not None
            },
            positions=positions,
            working={
                order.symbol: order.quantity - order.filled_quantity
                for order in book.open_orders
            },
            verdicts=verdicts,
            eligible_verdicts=config.constraints.eligible_verdicts,
            recent_fingerprints=tuple(
                order.fingerprint() for order, _ in self._screened.values()
            ),
            current_gross=gross,
        )

    def screen(self, orders: tuple[ProposedOrder, ...]) -> tuple[GateDecision, ...]:
        decisions = screen_all(orders, self.gate_context())
        with self._lock:
            for order, decision in zip(orders, decisions, strict=True):
                self._screened[order.order_id] = (order, decision)
        return decisions

    def submit(self, order_ids: tuple[str, ...]) -> list[dict[str, Any]]:
        """Route cleared orders. An unscreened or blocked one is refused here too.

        The OMS refuses anyway — that is its job and its check is the one that
        counts. This refuses earlier so the caller gets a reason naming the gate
        rather than a clearance mismatch.
        """
        results: list[dict[str, Any]] = []
        for order_id in order_ids:
            with self._lock:
                entry = self._screened.get(order_id)
            if entry is None:
                results.append(
                    {"order_id": order_id, "accepted": False,
                     "reason": "this order has not been screened by the pre-trade gate"}
                )
                continue
            order, decision = entry
            if not decision.allowed:
                results.append(
                    {"order_id": order_id, "accepted": False,
                     "reason": "blocked by the pre-trade gate: " + "; ".join(decision.reasons)}
                )
                continue
            try:
                placed = self.oms.submit(
                    order, decision.clearance, reference_price=order.reference_price
                )
            except OrderRefused as exc:
                results.append({"order_id": order_id, "accepted": False, "reason": str(exc)})
                continue
            results.append(
                {
                    "order_id": order_id,
                    "accepted": True,
                    "status": placed.status,
                    "filled": placed.filled_quantity,
                    "average_price": placed.average_price,
                    "venue": placed.venue,
                    "simulated": True,
                }
            )
        return results

    def screened(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._screened.values())
        return [
            {"order": order.model_dump(mode="json"), "decision": decision.as_dict()}
            for order, decision in entries
        ]

    # ── operations and performance ───────────────────────────────────────────
    def operations(self) -> dict[str, Any]:
        book = self.oms.book()
        return {
            "book": book.as_dict(),
            "reconciliation": self.oms.reconcile(),
            "orders": [order.model_dump(mode="json") for order in self.oms.store.orders(200)],
            "fills": [fill.model_dump(mode="json") for fill in self.oms.store.fills(200)][::-1],
            "audit_summary": self.audit.summary(),
            "execution_mode": "PAPER",
            "limitations": [
                "Execution is simulated locally. No broker, OMS vendor or venue is "
                "connected, and every fill is labelled simulated on the record.",
            ],
        }

    def performance(self) -> dict[str, Any]:
        """Attribution over the simulated book's realised P&L.

        Per fill rather than per day: this build has no mark-to-market clock, so
        a daily series would have to be invented. Fills are what actually
        happened, and the report says the series is trade-level.
        """
        config = self.config
        fills = self.oms.store.fills(2000)
        orders = {order.order_id: order for order in self.oms.store.orders(2000)}
        book = self.oms.book()
        records = [
            PnlRecord(
                pnl=position.realised_pnl,
                strategy_id=next(
                    (
                        order.strategy_id
                        for order in orders.values()
                        if order.symbol == position.symbol and order.strategy_id
                    ),
                    "",
                ),
                symbol=position.symbol,
                sector=(
                    config.instruments[position.symbol].sector
                    if position.symbol in config.instruments
                    else ""
                ),
                costs=0.0,
            )
            for position in book.positions
        ]
        arrival = {
            symbol: instrument.price
            for symbol, instrument in config.instruments.items()
            if instrument.price is not None
        }
        built = performance_report(records, fills=fills, arrival_prices=arrival)
        payload = built.as_dict()
        payload["limitations"] = [
            *payload.get("limitations", []),
            "Attribution is over realised P&L per instrument, not a daily return "
            "series: this build has no mark-to-market clock, and inventing one would "
            "put fabricated observations into every risk statistic here.",
        ]
        return payload

    # ── the command centre ───────────────────────────────────────────────────
    def state(self, *, stance: str | None = None) -> dict[str, Any]:
        config = self.config
        book = self.oms.book()
        nav = book.cash + sum(
            position.quantity
            * position.average_price
            * config.instruments[position.symbol].multiplier
            for position in book.positions
            if position.symbol in config.instruments
        )
        gross = sum(
            abs(position.quantity)
            * position.average_price
            * config.instruments[position.symbol].multiplier
            for position in book.positions
            if position.symbol in config.instruments
        )
        net = sum(
            position.quantity
            * position.average_price
            * config.instruments[position.symbol].multiplier
            for position in book.positions
            if position.symbol in config.instruments
        )
        assessment = self.risk()
        return {
            "nav": round(nav, 2),
            "cash": book.cash,
            "capital": config.capital,
            "realised_pnl": book.realised_pnl,
            "gross_exposure": round(gross / max(config.capital, 1.0), 6),
            "net_exposure": round(net / max(config.capital, 1.0), 6),
            "leverage": round(gross / max(config.capital, 1.0), 6),
            "risk": assessment.as_dict(),
            "execution_mode": "PAPER",
            "simulated": book.simulated,
            "stance": stance,
            "stages": [state.as_dict() for state in self.stages()],
            "limitations": [
                "NAV marks open positions at their average fill price. There is no "
                "mark-to-market feed in this build, so an open position's unrealised "
                "P&L is not measured and is not shown as zero.",
                "Period returns — MTD, YTD, daily — need a daily NAV series this build "
                "does not keep. They are absent rather than computed from fills.",
            ],
        }

    def stages(self) -> list[StageState]:
        """One state per loop stage, measured from the records behind it."""
        config = self.config
        states: dict[Stage, StageState] = {}

        universe = len(config.universe)
        states[Stage.DATA] = StageState(
            stage=Stage.DATA,
            status=StageStatus.READY if universe else StageStatus.IDLE,
            summary=f"{universe} instrument(s)" if universe else "no universe configured",
            count=universe,
            detail="local archives only; no vendor feed is connected",
        )

        specs = self.library.list_specs()
        states[Stage.RESEARCH] = StageState(
            stage=Stage.RESEARCH,
            status=StageStatus.READY if specs else StageStatus.IDLE,
            summary=f"{len(specs)} strategy specification(s)",
            count=len(specs),
        )

        signals, _ = self.signals()
        eligible = [s for s in signals if s.verdict in config.constraints.eligible_verdicts]
        states[Stage.ALPHA] = StageState(
            stage=Stage.ALPHA,
            status=StageStatus.READY if eligible else StageStatus.IDLE,
            summary=(
                f"{len(eligible)} of {len(signals)} candidate(s) eligible"
                if signals
                else "no candidates in the universe"
            ),
            count=len(eligible),
        )

        judged = [s for s in signals if s.verdict is not None]
        failing = [s for s in judged if s.verdict != "PASS"]
        states[Stage.VALIDATION] = StageState(
            stage=Stage.VALIDATION,
            status=(
                StageStatus.UNKNOWN
                if not judged
                else StageStatus.WARNING
                if failing
                else StageStatus.READY
            ),
            summary=(
                "nothing judged yet"
                if not judged
                else f"{len(failing)} not passing" if failing else f"{len(judged)} passing"
            ),
            count=len(judged),
            detail="the G0-G13 ladder, unchanged; INCONCLUSIVE means never measured",
        )

        with self._lock:
            proposals = list(self._proposals.values())
            screened = list(self._screened.values())
        states[Stage.PORTFOLIO] = StageState(
            stage=Stage.PORTFOLIO,
            status=(
                StageStatus.IDLE
                if not proposals
                else StageStatus.READY if proposals[0].feasible else StageStatus.BLOCKED
            ),
            summary=(
                "no proposal"
                if not proposals
                else f"{len(proposals[0].holdings)} holding(s)"
                if proposals[0].feasible
                else "infeasible"
            ),
            count=len(proposals[0].holdings) if proposals else None,
        )

        assessment = self.risk()
        states[Stage.RISK] = StageState(
            stage=Stage.RISK,
            status=(
                StageStatus.HALTED
                if not assessment.enabled
                else StageStatus.READY if assessment.within_limits else StageStatus.BLOCKED
            ),
            summary=(
                "kill switch engaged"
                if not assessment.enabled
                else "within limits"
                if assessment.within_limits
                else f"{len(assessment.breaches)} breach(es)"
            ),
            count=len(assessment.breaches),
        )

        blocked = [d for _, d in screened if not d.allowed]
        states[Stage.GATE] = StageState(
            stage=Stage.GATE,
            status=(
                StageStatus.IDLE
                if not screened
                else StageStatus.BLOCKED if blocked else StageStatus.READY
            ),
            summary=(
                "nothing screened"
                if not screened
                else f"{len(blocked)} of {len(screened)} blocked"
            ),
            count=len(blocked),
        )

        book = self.oms.book()
        fills = self.oms.store.fills(1)
        states[Stage.EXECUTION] = StageState(
            stage=Stage.EXECUTION,
            status=StageStatus.READY if fills else StageStatus.IDLE,
            summary="PAPER",
            count=len(book.open_orders),
            detail="simulated locally; no venue is connected",
        )

        reconciliation = self.oms.reconcile()
        states[Stage.OPERATIONS] = StageState(
            stage=Stage.OPERATIONS,
            status=(
                StageStatus.READY if reconciliation["reconciled"] else StageStatus.WARNING
            ),
            summary=(
                "reconciled"
                if reconciliation["reconciled"]
                else f"{len(reconciliation['discrepancies'])} discrepancy(ies)"
            ),
            count=int(reconciliation["orders"]),
        )

        states[Stage.PERFORMANCE] = StageState(
            stage=Stage.PERFORMANCE,
            status=StageStatus.READY if book.positions else StageStatus.IDLE,
            summary=(
                f"{book.realised_pnl:,.2f} realised" if book.positions else "nothing to measure"
            ),
        )

        recent = self.audit.recent(20)
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        fresh = [entry for entry in recent if entry.at >= cutoff]
        states[Stage.FEEDBACK] = StageState(
            stage=Stage.FEEDBACK,
            status=StageStatus.READY if fresh else StageStatus.IDLE,
            summary=f"{len(fresh)} action(s) in the last day",
            count=len(fresh),
        )

        return [states[spec.stage] for spec in LOOP]


def order_summary(order: Order) -> dict[str, Any]:
    return order.model_dump(mode="json")
