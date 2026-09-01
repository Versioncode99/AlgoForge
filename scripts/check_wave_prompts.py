from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- prompt:start -->"
END = "<!-- prompt:end -->"


def prompt_word_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    body = text.split(START, 1)[1].split(END, 1)[0]
    return len(re.findall(r"\S+", body))


def main() -> int:
    files = sorted((ROOT / "prompts" / "waves").glob("*.md"))
    results = {path.name: prompt_word_count(path) for path in files}
    for name, count in results.items():
        print(f"{name}: {count}")
    if len(files) != 10 or any(count != 250 for count in results.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
