"""A provenance-preserving scholarly library. Retrieved text is untrusted evidence."""

from __future__ import annotations

import builtins
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from forge.contracts.hashing import stable_id

SEEDS: tuple[tuple[str, str, str, int, str, str, str, list[str], str], ...] = (
    (
        "tsmom",
        "Time Series Momentum",
        "Moskowitz, Ooi & Pedersen",
        2012,
        "https://www.aqr.com/insights/research/journal-article/time-series-momentum",
        "Return persistence across futures; volatility normalization separates signal and risk.",
        "Published horizons are months. Intraday single-contract adaptation is unvalidated.",
        ["vol_normalized_momentum"],
        "momentum",
    ),
    (
        "vol-managed",
        "Volatility Managed Portfolios",
        "Moreira & Muir",
        2016,
        "https://www.nber.org/papers/w22208",
        "Scale exposure down when estimated variance is high; risk need not track expected return.",
        "Our one-contract runtime uses a volatility gate, not the paper's portfolio sizing rule.",
        ["vol_normalized_momentum"],
        "volatility",
    ),
    (
        "variance-ratio",
        "Stock Market Prices Do Not Follow Random Walks",
        "Lo & MacKinlay",
        1987,
        "https://www.nber.org/papers/w2168",
        "Compare return variance across aggregation horizons to test serial dependence.",
        (
            "A variance-ratio regime filter is a hypothesis, not a profitable "
            "strategy proven by this test."
        ),
        ["variance_ratio_reversion"],
        "mean_reversion",
    ),
    (
        "ou-residual",
        "Statistical Arbitrage in the U.S. Equities Market",
        "Avellaneda & Lee",
        2010,
        "https://math.nyu.edu/inmemoriam/avellaneda/AvellanedaLeeStatArb20090616.pdf",
        "Model mean-reverting residuals after removing systematic equity exposures.",
        (
            "True residual stat-arb needs synchronized assets and factor "
            "exposures; a single-price OU fit is an adaptation."
        ),
        ["ou_half_life_reversion"],
        "mean_reversion",
    ),
    (
        "vwap-paper",
        "Volume Weighted Average Price: The Holy Grail for Day Trading Systems",
        "Zarattini & Aziz",
        2023,
        "https://concretumgroup.com/papers/",
        "Use a session VWAP trading rule as a reproducible intraday research baseline.",
        "A rolling VWAP deviation model is not a replication of the paper or its equity universe.",
        ["vwap_sigma_reversion"],
        "mean_reversion",
    ),
    (
        "dsr",
        "The Deflated Sharpe Ratio",
        "Bailey & Lopez de Prado",
        2014,
        "https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf",
        "Correct selected Sharpe estimates for multiple trials and non-normal returns.",
        "Requires the full trial history; a count of surviving strategies understates selection.",
        [],
        "validation",
    ),
    (
        "pbo",
        "The Probability of Backtest Overfitting",
        "Bailey et al.",
        2015,
        "https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf",
        "Compare in-sample winners and out-of-sample ranks using symmetric cross-validation.",
        "Correlated experiments and temporal leakage still need explicit control.",
        [],
        "validation",
    ),
    (
        "reversion-counter",
        "Mean Reversion in Stock Prices? A Reappraisal of the Empirical Evidence",
        "Kim, Nelson & Startz",
        1988,
        "https://www.nber.org/papers/w2795",
        "Evidence for mean reversion changes across historical subperiods.",
        "Counter-evidence: do not assume a stationary reversion mechanism across regimes.",
        ["ou_half_life_reversion", "variance_ratio_reversion"],
        "mean_reversion",
    ),
)


class ResearchLibrary:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT)")
        for key, title, authors, year, url, summary, gap, templates, topic in SEEDS:
            self.put(
                {
                    "id": key,
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "url": url,
                    "summary": summary,
                    "replication_gap": gap,
                    "templates": templates,
                    "topic": topic,
                    "origin": "curated",
                    "evidence": "RESEARCH_REFERENCE",
                    "content_level": "curated_summary",
                }
            )

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def put(self, item: dict[str, Any]) -> None:
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO sources VALUES (?, ?)", (item["id"], json.dumps(item))
            )

    def list(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as db, db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM sources ORDER BY rowid DESC LIMIT 500")
            ]

    def search(
        self, query: str, *, transport: httpx.BaseTransport | None = None
    ) -> builtins.list[dict[str, Any]]:
        """Fetch metadata/available abstracts; never claim to have read full papers.

        Fixed host, no redirects or arbitrary URL retrieval. No local data or secrets
        are sent. Crossref works without an API credential.
        """
        query = query.strip()[:240]
        if len(query) < 3:
            raise ValueError("Research query must contain at least three characters")
        with (
            httpx.Client(timeout=20, transport=transport, follow_redirects=False) as client,
            client.stream(
                "GET",
                "https://api.crossref.org/works",
                params={"query.bibliographic": query, "rows": 8},
                headers={"User-Agent": "AlgoForgeResearch/1.0"},
            ) as response,
        ):
            response.raise_for_status()
            chunks = bytearray()
            for chunk in response.iter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 2_000_000:
                    raise ValueError("Scholarly response exceeds the 2 MB limit")
            payload = json.loads(chunks)
        found = []
        for raw in payload.get("message", {}).get("items", [])[:8]:
            doi = str(raw.get("DOI", ""))
            if not doi or not raw.get("title"):
                continue
            abstract = re.sub("<[^>]+>", " ", str(raw.get("abstract", "")))[:6000]
            date_parts = raw.get("published", {}).get("date-parts", [[None]])
            item: dict[str, Any] = {
                "id": stable_id("paper", doi.lower()),
                "doi": doi,
                "title": str(raw["title"][0])[:500],
                "authors": ", ".join(str(a.get("family", "")) for a in raw.get("author", [])[:8]),
                "year": date_parts[0][0] if date_parts and date_parts[0] else None,
                "url": "https://doi.org/" + quote(doi, safe="/"),
                "summary": abstract or "No abstract supplied. Read the source before replication.",
                ("replication_gap"): (
                    "Discovered reference; methodology and data requirements need review."
                ),
                "templates": [],
                "topic": query,
                "origin": "crossref",
                "content_level": "abstract" if abstract else "metadata",
                "evidence": "UNREVIEWED",
                "retrieved_at": datetime.now(UTC).isoformat(),
            }
            self.put(item)
            found.append(item)
        return found
