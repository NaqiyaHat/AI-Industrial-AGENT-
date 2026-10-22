"""Search equipment manuals so answers can quote the real procedure.

A small keyword search (TF-IDF style) over Markdown manuals split by heading.
No external services or embeddings needed; for large document sets swap this
for a vector database without changing the callers.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "on", "in", "is", "what", "how", "do", "i", "with"}


@dataclass
class Passage:
    source: str
    heading: str
    text: str


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


class ManualIndex:
    def __init__(self, manuals_dir: str | Path):
        self.passages: list[Passage] = []
        for path in sorted(Path(manuals_dir).glob("*.md")):
            self.passages += _split(path)
        self._tf = [Counter(_tokens(p.heading + " " + p.text)) for p in self.passages]
        df = Counter(w for tf in self._tf for w in tf)
        n = len(self.passages) or 1
        self._idf = {w: math.log(1 + n / c) for w, c in df.items()}

    def search(self, query: str, k: int = 3) -> list[Passage]:
        terms = _tokens(query)
        scored = []
        for passage, tf in zip(self.passages, self._tf):
            score = sum(self._idf.get(t, 0) * (1 + math.log(tf[t])) for t in terms if tf[t])
            if score:
                scored.append((score, passage))
        scored.sort(key=lambda s: -s[0])
        return [p for _, p in scored[:k]]


def _split(path: Path) -> list[Passage]:
    """One passage per ``##`` section; the ``#`` title is kept as context."""
    title, heading, lines, out = path.stem, "", [], []
    for line in path.read_text().splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            if lines:
                out.append(Passage(path.name, f"{title} - {heading}", "\n".join(lines).strip()))
            heading, lines = line[3:].strip(), []
        else:
            lines.append(line)
    if lines:
        out.append(Passage(path.name, f"{title} - {heading}", "\n".join(lines).strip()))
    return out
