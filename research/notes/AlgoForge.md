---
type: "algoforge-index"
updated: "2026-09-14T01:11:17+00:00"
tags:
  - "algoforge"
  - "index"
---

# AlgoForge

Everything below was written by the application. The folders are mirrors: the
executable truth lives in `.store`, which Obsidian hides.

| Folder | Holds | Count |
| --- | --- | --- |
| `Strategies` | one note per strategy the engine wrote | 0 |
| `Research Papers` | curated and discovered references | 8 |
| `Backtests` | one note per completed run | 0 |
| `Verdicts` | the deterministic judge's decisions | 0 |
| `Families` | strategy families and their mechanisms | 12 |
| `Missions` | orchestrated multi-agent work | 0 |

## Useful queries

```dataview
TABLE family, template, created
FROM "10 AlgoForge/Strategies"
SORT created DESC
LIMIT 25
```

```dataview
TABLE verdict, strategy
FROM "10 AlgoForge/Verdicts"
WHERE verdict = "PASS"
```

> [!warning] Paper only
> AlgoForge models fills; it does not place orders. No note in this vault is
> financial advice or evidence of future profit.
