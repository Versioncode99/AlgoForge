"""External research retrieval, with provenance that cannot be faked.

The autonomous researcher is allowed to go and read. That capability is only
safe if the record it leaves behind can be checked, so this module holds one
rule above all the others:

    **Every stored source is something a fetch returned.** There is no code path
    that constructs a citation, and no fallback that invents one when the
    network is unavailable. With no network this module returns an empty list
    and the caller records the frontier item as blocked. It does not guess a
    plausible paper.

Two public indexes are used, both without credentials:

* **arXiv** — the q-fin listings, which is where quantitative finance
  preprints actually are. Returns titles, authors, abstracts and dates.
* **Crossref** — DOI metadata across publishers, which reaches the journal
  literature and, through their DOIs, SSRN working papers.

``forge.research.sources.ResearchLibrary`` already searches Crossref and stores
what it finds. This module does not replace it — it is the retrieval half that
the *engine* drives, and it adds what an autonomous researcher needs and a
human-driven search does not: **claim extraction**, so a mechanism can be read
out of an abstract and turned into a hypothesis, and **relevance scoring**, so
eight results can be ranked without a model.

**Claims are spans, never paraphrases.** ``extract_claims`` returns sentences
copied verbatim out of the retrieved abstract. A paraphrase would be the system
writing a sentence and attributing it to a paper, which is the same failure as
inventing the paper with extra steps. Every claim carries the offsets it was
taken from, so it can be checked against the stored abstract.

**Retrieved text is evidence, never instruction.** An abstract that contains
"ignore your previous instructions" is a string in a database. Nothing in this
module or its callers treats a fetched document as anything but data.
"""

from __future__ import annotations

import builtins
import json
import re
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from forge.contracts.hashing import stable_id

SCHEMA_VERSION = 1

#: Hard caps. A retrieval that runs away is a retrieval that hangs an engine
#: worker, and the useful part of an abstract is in its first few thousand
#: characters whatever the publisher sent.
MAX_RESPONSE_BYTES = 2_000_000
MAX_ABSTRACT = 6_000
MAX_RESULTS = 10
REQUEST_TIMEOUT = 20.0

#: Fixed hosts. No redirect following and no arbitrary URL retrieval: the agent
#: may search these indexes, it may not fetch a URL something told it to fetch.
ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"

USER_AGENT = "AlgoForgeResearch/2.0 (autonomous research; contact: local install)"

#: Sentence-ish split. Deliberately conservative — it errs towards longer spans,
#: because a truncated claim misrepresents a paper and an over-long one only
#: wastes space.
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")

#: What makes a sentence a claim about a mechanism rather than throat-clearing.
#: Matching one of these is necessary but not sufficient; the scoring below also
#: requires the sentence to carry a subject the query was about.
_CLAIM_MARKERS = (
    "we find",
    "we show",
    "we document",
    "results show",
    "evidence that",
    "is driven by",
    "is explained by",
    "predicts",
    "is associated with",
    "leads to",
    "consistent with",
    "significant",
    "outperform",
    "reverts",
    "persists",
    "premium",
    "anomaly",
    "explains",
)

#: Query terms that are about trading mechanisms rather than about papers.
#: Used for relevance only; nothing is filtered out on the strength of them.
_TOPIC_HINTS = (
    "intraday",
    "microstructure",
    "volatility",
    "momentum",
    "reversal",
    "reversion",
    "liquidity",
    "order flow",
    "opening",
    "close",
    "auction",
    "futures",
    "seasonality",
    "regime",
    "autocorrelation",
    "breakout",
    "volume",
    "overnight",
    "lead-lag",
)


class LiteratureError(RuntimeError):
    """Retrieval failed. The message says what and why."""


@dataclass(frozen=True)
class Claim:
    """One sentence copied verbatim out of a retrieved abstract."""

    text: str
    #: Character offsets into the stored abstract, so the copy can be checked.
    start: int
    end: int

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class Source:
    """A retrieved research source and everything recorded about it.

    ``retrieved_at`` is when *this installation* fetched it, and is always
    present. ``published`` is what the index reported and may be empty — a
    missing publication date is recorded as missing rather than guessed.
    """

    source_id: str
    title: str
    authors: str
    source: str
    url: str
    published: str
    retrieved_at: str
    abstract: str
    claims: tuple[Claim, ...]
    relevance: float
    query: str
    #: What was actually obtained. "abstract" means the abstract was returned by
    #: the index; it never means the full paper was read.
    content_level: str
    evidence: str = "UNREVIEWED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.source_id,
            "title": self.title,
            "authors": self.authors,
            "source": self.source,
            "url": self.url,
            "published": self.published,
            "retrieved_at": self.retrieved_at,
            "summary": self.abstract,
            "claims": [c.as_dict() for c in self.claims],
            "relevance": round(self.relevance, 4),
            "query": self.query,
            "content_level": self.content_level,
            "evidence": self.evidence,
            "origin": self.source,
            "templates": [],
            "replication_gap": (
                "Retrieved metadata and abstract only. Method, data and cost assumptions "
                "must be read from the source before any replication claim is made."
            ),
        }


def _clean(text: str, limit: int = MAX_ABSTRACT) -> str:
    """Strip markup and collapse whitespace, without rewording anything."""
    stripped = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", stripped).strip()[:limit]


def extract_claims(abstract: str, *, query: str = "", limit: int = 4) -> tuple[Claim, ...]:
    """Sentences from ``abstract`` that state a finding, copied exactly.

    Scored on two things: whether the sentence contains a marker that makes it a
    claim rather than a description of the paper's structure, and whether it
    shares vocabulary with the query. Nothing is rewritten, joined or
    summarised — a claim is a substring of the abstract and the offsets prove it.
    """
    if not abstract:
        return ()
    terms = {w for w in re.findall(r"[a-z]{4,}", query.lower())}
    scored: list[tuple[float, Claim]] = []
    cursor = 0
    for sentence in _SENTENCE.split(abstract):
        start = abstract.find(sentence, cursor)
        if start < 0:
            continue
        cursor = start + len(sentence)
        text = sentence.strip()
        if not 40 <= len(text) <= 600:
            continue
        lowered = text.lower()
        markers = sum(1 for marker in _CLAIM_MARKERS if marker in lowered)
        if not markers:
            continue
        overlap = sum(1 for term in terms if term in lowered)
        topical = sum(1 for hint in _TOPIC_HINTS if hint in lowered)
        score = markers * 1.0 + overlap * 0.6 + topical * 0.4
        scored.append((score, Claim(text=text, start=start, end=start + len(text))))
    scored.sort(key=lambda pair: -pair[0])
    # Restore document order among the winners: claims read as an argument, and
    # an argument reordered by score reads as noise.
    chosen = sorted((claim for _, claim in scored[:limit]), key=lambda c: c.start)
    return tuple(chosen)


def score_relevance(title: str, abstract: str, query: str) -> float:
    """How closely a result matches what was asked, in [0, 1].

    Title matches count double: an index returning a paper whose title contains
    the query terms is a much stronger signal than one that mentions them once
    in an abstract.
    """
    terms = {w for w in re.findall(r"[a-z]{4,}", query.lower())}
    if not terms:
        return 0.0
    title_lower, abstract_lower = title.lower(), abstract.lower()
    hits = sum(2.0 for t in terms if t in title_lower) + sum(
        1.0 for t in terms if t in abstract_lower
    )
    topical = sum(0.5 for hint in _TOPIC_HINTS if hint in f"{title_lower} {abstract_lower}")
    return min(1.0, (hits + topical) / (2.0 * len(terms) + 2.0))


def _fetch(
    url: str, params: dict[str, Any], *, transport: httpx.BaseTransport | None = None
) -> bytes:
    """One bounded GET against a fixed host. No redirects, no local data sent."""
    with (
        httpx.Client(
            timeout=REQUEST_TIMEOUT, transport=transport, follow_redirects=False
        ) as client,
        client.stream("GET", url, params=params, headers={"User-Agent": USER_AGENT}) as response,
    ):
        response.raise_for_status()
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) > MAX_RESPONSE_BYTES:
                raise LiteratureError(
                    f"response from {url} exceeded the {MAX_RESPONSE_BYTES:,} byte limit"
                )
    return bytes(chunks)


_ATOM = "{http://www.w3.org/2005/Atom}"


def search_arxiv(
    query: str, *, limit: int = 8, transport: httpx.BaseTransport | None = None
) -> list[Source]:
    """Search arXiv's quantitative-finance listings.

    Scoped to the ``q-fin`` and ``stat.AP`` categories rather than the whole of
    arXiv: an unscoped query for "momentum" returns particle physics, and
    filtering afterwards would mean fetching an order of magnitude more than is
    needed.
    """
    query = query.strip()[:240]
    if len(query) < 3:
        raise LiteratureError("A literature query must contain at least three characters.")
    payload = _fetch(
        ARXIV_API,
        {
            "search_query": f"(cat:q-fin* OR cat:stat.AP) AND all:{query}",
            "start": 0,
            "max_results": min(limit, MAX_RESULTS),
            "sortBy": "relevance",
        },
        transport=transport,
    )
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise LiteratureError(f"arXiv returned unparseable XML: {exc}") from exc

    retrieved_at = datetime.now(UTC).isoformat()
    found: list[Source] = []
    for entry in root.findall(f"{_ATOM}entry"):
        title = _clean(entry.findtext(f"{_ATOM}title", ""), 500)
        link = entry.findtext(f"{_ATOM}id", "") or ""
        if not title or not link:
            continue
        abstract = _clean(entry.findtext(f"{_ATOM}summary", ""))
        authors = ", ".join(
            _clean(a.findtext(f"{_ATOM}name", ""), 80) for a in entry.findall(f"{_ATOM}author")[:8]
        )
        published = (entry.findtext(f"{_ATOM}published", "") or "")[:10]
        found.append(
            Source(
                source_id=stable_id("arxiv", link),
                title=title,
                authors=authors,
                source="arxiv",
                url=link,
                published=published,
                retrieved_at=retrieved_at,
                abstract=abstract,
                claims=extract_claims(abstract, query=query),
                relevance=score_relevance(title, abstract, query),
                query=query,
                content_level="abstract" if abstract else "metadata",
            )
        )
    return found


def search_crossref(
    query: str, *, limit: int = 8, transport: httpx.BaseTransport | None = None
) -> list[Source]:
    """Search Crossref DOI metadata.

    Reaches the published journal literature and, via their registered DOIs,
    SSRN working papers. Abstracts are present for some publishers and absent
    for others; an absent one is recorded as ``metadata`` rather than filled in.
    """
    query = query.strip()[:240]
    if len(query) < 3:
        raise LiteratureError("A literature query must contain at least three characters.")
    payload = _fetch(
        CROSSREF_API,
        {"query.bibliographic": query, "rows": min(limit, MAX_RESULTS)},
        transport=transport,
    )
    try:
        items = json.loads(payload).get("message", {}).get("items", [])
    except json.JSONDecodeError as exc:
        raise LiteratureError(f"Crossref returned unparseable JSON: {exc}") from exc

    retrieved_at = datetime.now(UTC).isoformat()
    found: list[Source] = []
    for raw in items[:limit]:
        doi = str(raw.get("DOI", ""))
        titles = raw.get("title") or []
        if not doi or not titles:
            continue
        title = _clean(str(titles[0]), 500)
        abstract = _clean(str(raw.get("abstract", "")))
        parts = (raw.get("published") or {}).get("date-parts") or [[]]
        published = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts[0][:3]))
        publisher = _clean(str(raw.get("publisher", "")), 120)
        found.append(
            Source(
                source_id=stable_id("paper", doi.lower()),
                title=title,
                authors=", ".join(
                    _clean(str(a.get("family", "")), 80) for a in (raw.get("author") or [])[:8]
                ),
                source=f"crossref:{publisher}" if publisher else "crossref",
                url="https://doi.org/" + quote(doi, safe="/"),
                published=published,
                retrieved_at=retrieved_at,
                abstract=abstract,
                claims=extract_claims(abstract, query=query),
                relevance=score_relevance(title, abstract, query),
                query=query,
                content_level="abstract" if abstract else "metadata",
            )
        )
    return found


def search(
    query: str,
    *,
    limit: int = 8,
    transport: httpx.BaseTransport | None = None,
) -> list[Source]:
    """Both indexes, merged, deduplicated by title, best match first.

    An index that fails is skipped rather than fatal — arXiv being unreachable
    should not cost the Crossref results. If *both* fail, the error propagates:
    silently returning nothing would be indistinguishable from "the literature
    contains nothing on this", which is a very different fact.
    """
    results: list[Source] = []
    failures: list[str] = []
    for name, fetcher in (("arxiv", search_arxiv), ("crossref", search_crossref)):
        try:
            results.extend(fetcher(query, limit=limit, transport=transport))
        except Exception as exc:  # network, parse, HTTP status
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if not results and failures:
        raise LiteratureError("; ".join(failures))

    seen: set[str] = set()
    unique: list[Source] = []
    for item in sorted(results, key=lambda s: -s.relevance):
        key = re.sub(r"[^a-z0-9]", "", item.title.lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:limit]


@dataclass
class RetrievalReport:
    """What one literature query actually produced.

    Recorded on the frontier item that prompted it, so "the engine searched and
    found nothing" is a stored fact rather than an absence of stored facts.
    """

    query: str
    found: tuple[Source, ...] = ()
    error: str = ""
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    @property
    def ok(self) -> bool:
        return not self.error

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "found": [s.as_dict() for s in self.found],
            "count": len(self.found),
            "claims": sum(len(s.claims) for s in self.found),
            "error": self.error,
            "at": self.at,
        }


def retrieve(
    query: str, *, limit: int = 8, transport: httpx.BaseTransport | None = None
) -> RetrievalReport:
    """Search, and report the failure rather than raising it.

    The engine calls this on a worker thread inside a research cycle. A network
    error there must degrade the cycle to "no external evidence this time", not
    kill the worker — but it must also be *visible*, which is why the error text
    is carried rather than swallowed.
    """
    try:
        return RetrievalReport(
            query=query, found=tuple(search(query, limit=limit, transport=transport))
        )
    except Exception as exc:
        return RetrievalReport(query=query, error=f"{type(exc).__name__}: {exc}")


class SourceStore:
    """Retrieved sources, kept with their provenance.

    Separate from ``forge.research.sources.ResearchLibrary``, which holds the
    curated seed list and the operator's own searches. This one holds what the
    autonomous researcher retrieved, with the claims it extracted and the query
    that produced it, so a generated hypothesis can be traced to the sentence
    that suggested it.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sources ("
                "source_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, payload TEXT NOT NULL, "
                "relevance REAL NOT NULL, schema_version INTEGER NOT NULL, at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS retrievals ("
                "retrieval_id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL, "
                "query TEXT NOT NULL, count INTEGER NOT NULL, error TEXT NOT NULL, "
                "at TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def record(self, campaign_id: str, report: RetrievalReport) -> list[Source]:
        """Store a retrieval and its results. Returns what was stored."""
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO retrievals (campaign_id, query, count, error, at) VALUES (?,?,?,?,?)",
                (campaign_id, report.query, len(report.found), report.error, report.at),
            )
            for item in report.found:
                db.execute(
                    "INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?)",
                    (
                        item.source_id,
                        campaign_id,
                        json.dumps(item.as_dict()),
                        item.relevance,
                        SCHEMA_VERSION,
                        item.retrieved_at,
                    ),
                )
        return list(report.found)

    def list(
        self, campaign_id: str | None = None, *, limit: int = 200
    ) -> builtins.list[dict[str, Any]]:
        where, args = ("WHERE campaign_id=?", (campaign_id,)) if campaign_id else ("", ())
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT payload FROM sources {where} ORDER BY relevance DESC, at DESC LIMIT ?",
                (*args, int(limit)),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get(self, source_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT payload FROM sources WHERE source_id=?", (source_id,)
            ).fetchone()
        return None if row is None else json.loads(row["payload"])

    def queries(
        self, campaign_id: str | None = None, *, limit: int = 100
    ) -> builtins.list[dict[str, Any]]:
        """Every query run, including the ones that found nothing.

        The empty results are the point. A frontier that says "searched, found
        nothing" is different from one that never looked, and only a stored
        query can tell them apart.
        """
        where, args = ("WHERE campaign_id=?", (campaign_id,)) if campaign_id else ("", ())
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM retrievals {where} ORDER BY retrieval_id DESC LIMIT ?",
                (*args, int(limit)),
            ).fetchall()
        return [
            {
                "query": row["query"],
                "count": int(row["count"]),
                "error": row["error"],
                "at": row["at"],
            }
            for row in rows
        ]

    def count(self, campaign_id: str | None = None) -> int:
        where, args = ("WHERE campaign_id=?", (campaign_id,)) if campaign_id else ("", ())
        with closing(self._connect()) as db:
            row = db.execute(f"SELECT COUNT(*) AS n FROM sources {where}", args).fetchone()
        return int(row["n"]) if row else 0


#: Mechanism topics a campaign explores when it has no better lead. Each is a
#: real research area with a published literature, phrased as the search that
#: would find it. Not a fixed rota: the director draws from these only when the
#: frontier has produced no more specific question.
SEED_TOPICS: tuple[str, ...] = (
    "intraday return seasonality futures",
    "opening auction liquidity price impact",
    "volatility clustering intraday forecasting",
    "volatility expansion regime transition returns",
    "time series momentum futures horizon",
    "short horizon reversal liquidity provision",
    "volume price impact order flow imbalance",
    "closing auction rebalancing flow",
    "overnight versus intraday returns",
    "range compression breakout continuation",
    "realized volatility autocorrelation persistence",
    "index futures lead lag price discovery",
    "calendar effects futures settlement",
    "market maker inventory mean reversion",
)


def topics_for(objective: str, *, extra: Iterable[str] = ()) -> list[str]:
    """Search topics for a campaign, ordered by fit to its stated objective.

    Deterministic and offline. A campaign about intraday NQ gets the intraday
    and microstructure topics first; one about overnight effects gets those.
    """
    terms = {w for w in re.findall(r"[a-z]{4,}", objective.lower())}
    candidates = list(SEED_TOPICS) + [t for t in extra if t]

    def fit(topic: str) -> float:
        words = set(re.findall(r"[a-z]{4,}", topic.lower()))
        return len(words & terms) / max(1, len(words))

    return sorted(candidates, key=lambda t: (-fit(t), t))


def sources_to_ids(sources: Sequence[Source]) -> tuple[str, ...]:
    return tuple(s.source_id for s in sources)
