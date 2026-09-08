from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- prompt:start -->"
END = "<!-- prompt:end -->"

#: Each prompt set, with the shape it is required to hold. The wave prompts are
#: the ten-part build brief at 250 words each. The interface prompts are the
#: five-part brief the shell overhaul was written against, at 300 words each --
#: checked here for the same reason as the waves: a brief whose length is
#: asserted in a commit message and nowhere else drifts on the first edit.
SETS = (
    ("waves", 10, 250),
    ("interface", 5, 300),
)


def prompt_word_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    body = text.split(START, 1)[1].split(END, 1)[0]
    return len(re.findall(r"\S+", body))


def main() -> int:
    failed = False
    for folder, expected_files, expected_words in SETS:
        files = sorted((ROOT / "prompts" / folder).glob("*.md"))
        results = {path.name: prompt_word_count(path) for path in files}
        for name, count in results.items():
            flag = "" if count == expected_words else f"  <- expected {expected_words}"
            print(f"{folder}/{name}: {count}{flag}")
        if len(files) != expected_files:
            print(f"{folder}: {len(files)} prompts, expected {expected_files}")
            failed = True
        if any(count != expected_words for count in results.values()):
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
