"""The official rulebooks, looked up on demand: a few passages with book and printed page.

The engine implements the rules it runs every turn (rules/). Some rulings need a rule it does not
implement: drowning, poison, falling, fire, a spell, a phobia's effect, a car crash. Then the arbiter
asks for it (Ruling.lookup, agents/cast.Cast.arbiter), and so does a player's /kp question
(Cast.keeper), and this module answers with the passages that match. Not every turn, and without
an index (a player without the books) nothing asks: everything runs as before.

`autokeeper rules index` reads the books in paths.rulebooks() (the checkout's rule/ folder, which
is never committed): PDFs through pypdfium2, Word files through markitdown (an old .doc is first
converted to .docx by LibreOffice when it is installed), Markdown and text as they are. Each page
is cut into passages of a few hundred characters that never cross a page, so every passage cites
one printed page (read off the page's running header or footer; the PDF page when there is none),
with the chapter and the nearest heading. The passages are embedded locally (fastembed, the same
BAAI/bge-small-zh-v1.5 as the module knowledge base, knowledge.py) when fastembed is installed; a
lookup ranks by those embeddings plus overlapping words, or by the words alone without them.

LightRAG (knowledge.py) is not used here: its graph needs a model call per chunk to extract
entities, and a rules lookup wants the passage itself, not a graph of it. So: no model calls, ever.

Index files (paths.rules_index(), under data/cache, safe to delete): passages.jsonl, vectors.npy,
meta.json.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import paths

SUFFIXES = (".pdf", ".doc", ".docx", ".md", ".txt")
# A short title for each book the user supplied, by a word in its file name; others by file name.
TITLES = (("守秘人规则书", "守秘人规则书"), ("调查员手册", "调查员手册"), ("魔法大典", "克苏鲁神话魔法大典"),
          ("怪物之锤", "怪物之锤"), ("Dreamlands", "幻梦境"), ("时空穿梭", "克苏鲁时空穿梭"))
TARGET, LONGEST, OVERLAP = 480, 760, 60  # passage length in characters
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："  # bge-zh's instruction for short queries
WORD_WEIGHT = 0.35  # of the word score beside the embedding's
SCORE_FLOOR = 0.35  # below this, a passage is not about the question
HITS = 3
# Characters of a question's grammar (怎么办、是不是): a word with one of them is not a term.
STOP = set("的了是在吗呢吧啊怎么办什如何样哪谁会能要就都也还和与及或被把给对这那个些我你他她它们有没请问")

Embedder = Callable[[list[str]], Any]  # texts -> array (n, dim)


@dataclass
class Passage:
    book: str
    text: str
    page: int | None = None  # the printed page
    pdf_page: int | None = None
    chapter: str = ""
    section: str = ""
    file: str = ""

    def cite(self) -> str:
        where = f"第 {self.page} 页" if self.page else f"PDF 第 {self.pdf_page} 页" if self.pdf_page else ""
        return "，".join(x for x in (f"《{self.book}》{where}", self.chapter, self.section if self.section != self.chapter else "")
                        if x)


def title(path: Path) -> str:
    return next((name for word, name in TITLES if word in path.name), path.stem)


def books(folder: Path) -> list[Path]:
    """The books in `folder`, in a stable order: the Keeper Rulebook first, then by name."""
    if not folder.is_dir():
        return []
    found = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES]
    return sorted(found, key=lambda p: ("守秘人规则书" not in p.name, p.name))


# -- reading ------------------------------------------------------------------------------------
_CHAPTER = re.compile(r"^(第[一二三四五六七八九十百零〇\d]+章\s*\S.{0,20}|Chapter\s+\d+[:.]?\s*\S.{0,40}|CHAPTER\s+\w+.{0,40})$")
_NUMBER = re.compile(r"^\d{1,4}$")
_PUNCT = re.compile(r"[。，、；：！？,;:“”\"（）()《》·…—]")


def pdf_pages(path: Path) -> list[str]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        out = []
        for page in pdf:
            text = page.get_textpage().get_text_range()
            out.append(text.replace("\r\n", "\n").replace("\r", "\n"))
        return out
    finally:
        pdf.close()


def word_text(path: Path) -> str:
    """A Word file's text through markitdown; an old binary .doc goes through LibreOffice first
    (markitdown reads only .docx). ValueError with what to do when it cannot be read."""
    from markitdown import MarkItDown

    if path.suffix.lower() != ".doc":
        return MarkItDown().convert(str(path)).text_content
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if office is None:
        raise ValueError("读不了 .doc：装上 LibreOffice，或用 Word、WPS 另存为 .docx")
    with tempfile.TemporaryDirectory(dir=paths.cache()) as tmp:
        subprocess.run([office, "--headless", "--convert-to", "docx", "--outdir", tmp, str(path)],
                       check=True, capture_output=True, timeout=900)
        converted = Path(tmp) / (path.stem + ".docx")
        if not converted.exists():
            raise ValueError("LibreOffice 没能把 .doc 转成 .docx")
        return MarkItDown().convert(str(converted)).text_content


def _headers(pages: list[list[str]]) -> set[str]:
    """The running header lines of a book: a short line that opens many pages."""
    tops = Counter(line for lines in pages for line in lines[:3] if len(line) <= 24 and not _NUMBER.match(line))
    return {line for line, n in tops.items() if n >= 3}


def _printed(lines: list[str], index: int) -> int | None:
    """The page number printed on a page: a bare number among its first or last lines, close to
    where the PDF page is (front matter shifts it a little)."""
    for line in [*lines[:3], *lines[-3:]]:
        if _NUMBER.match(line) and index - 40 <= int(line) <= index + 5:
            return int(line)
    return None


def _heading(line: str, headers: set[str]) -> bool:
    return (2 <= len(line) <= 14 and not _PUNCT.search(line) and not re.search(r"\d|[Dd]\d", line)
            and line not in headers and not line.isascii())


def _join(lines: Iterable[str]) -> str:
    """A page's wrapped lines as running text: Chinese lines join without a space."""
    out = ""
    for line in lines:
        if out and not (out[-1].isascii() and line[:1].isascii()):
            out += line
        else:
            out += (" " if out else "") + line
    return out


def _chunks(text: str) -> list[str]:
    """Passages of about TARGET characters, cut after a sentence where one ends in reach."""
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + LONGEST)
        if end < len(text):
            cut = max(text.rfind(mark, start + TARGET // 2, end) for mark in ("。", "！", "？", ". ", "；"))
            end = cut + 1 if cut > 0 else min(len(text), start + TARGET)
        piece = text[start:end].strip()
        if len(piece) >= 30:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - OVERLAP, start + 1)
    return out


def split_pages(book: str, pages: list[str], *, file: str = "") -> list[Passage]:
    """A paged book (a PDF) as passages, each within one page."""
    lines = [[line.strip() for line in page.split("\n") if line.strip()] for page in pages]
    headers = _headers(lines)
    out: list[Passage] = []
    chapter = section = ""
    for index, rows in enumerate(lines, start=1):
        printed = _printed(rows, index)
        body = []
        for row in rows:
            if _CHAPTER.match(row):
                chapter = row
            if (_NUMBER.match(row) and printed is not None and int(row) == printed) or row in headers:
                continue
            if _heading(row, headers):
                section = row
            body.append(row)
        for piece in _chunks(_join(body)):
            out.append(Passage(book=book, text=piece, page=printed, pdf_page=index, chapter=chapter, section=section,
                               file=file))
    return out


def split_text(book: str, text: str, *, file: str = "") -> list[Passage]:
    """An unpaged book (Word, Markdown) as passages, under its headings."""
    out: list[Passage] = []
    chapter = section = ""
    body: list[str] = []

    def flush() -> None:
        for piece in _chunks(_join(body)):
            out.append(Passage(book=book, text=piece, chapter=chapter, section=section, file=file))
        body.clear()

    for raw in text.split("\n"):
        line = raw.strip().strip("*").strip()
        if not line or set(line) <= set("|-: "):
            continue
        heading = raw.lstrip().startswith("#")
        line = line.lstrip("#").strip()
        if heading or _CHAPTER.match(line) or _heading(line, set()):
            flush()
            if _CHAPTER.match(line) or raw.lstrip().startswith("# "):
                chapter, section = line, ""
            else:
                section = line
            continue
        body.append(line)
    flush()
    return out


def read(path: Path) -> list[Passage]:
    book = title(path)
    if path.suffix.lower() == ".pdf":
        return split_pages(book, pdf_pages(path), file=path.name)
    if path.suffix.lower() in (".doc", ".docx"):
        return split_text(book, word_text(path), file=path.name)
    return split_text(book, path.read_text(encoding="utf-8", errors="replace"), file=path.name)


# -- embedding ----------------------------------------------------------------------------------
def local_embedder() -> Embedder | None:
    """fastembed's local model (knowledge.EMBED_MODEL), or None when fastembed is not installed
    (uv sync --extra graphrag)."""
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return None
    from . import knowledge

    def embed(texts: list[str]):
        import numpy as np

        return np.array(list(knowledge._model().embed(texts, batch_size=64)))

    return embed


def _normalized(vectors):
    import numpy as np

    vectors = np.asarray(vectors, dtype="float32")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


# -- the index ----------------------------------------------------------------------------------
def _stamp(path: Path) -> dict:
    stat = path.stat()
    return {"size": stat.st_size, "mtime": int(stat.st_mtime)}


def build(folder: Path | None = None, out: Path | None = None, *, embedder: Embedder | str | None = "auto",
          force: bool = False, say: Callable[[str], None] = lambda _: None) -> dict:
    """Index the books in `folder` into `out`. A book that has not changed since the last index
    is taken from it, unless `force`. Returns what was indexed: books, passages, seconds."""
    import numpy as np

    folder = folder or paths.rulebooks()
    out = out or paths.rules_index()
    embed = local_embedder() if embedder == "auto" else embedder
    started = time.monotonic()
    old_meta = _meta(out)
    old = load(out, embedder=None) if not force and old_meta and bool(old_meta.get("embed")) == bool(embed) else None
    passages: list[Passage] = []
    vectors: list[Any] = []
    meta: dict[str, Any] = {"books": {}, "skipped": {}, "embed": None}
    for path in books(folder):
        stamp = _stamp(path)
        kept = old_meta.get("books", {}).get(path.name) if old is not None else None
        if kept and kept.get("stamp") == stamp:
            mine = [i for i, p in enumerate(old.passages) if p.file == path.name]  # type: ignore[union-attr]
            got = [old.passages[i] for i in mine]  # type: ignore[union-attr]
            vecs = old.vectors[mine] if old.vectors is not None and mine else None  # type: ignore[union-attr]
            say(f"{title(path)}：未变，沿用 {len(got)} 段")
        else:
            say(f"{title(path)}：读取中…")
            try:
                got = read(path)
            except Exception as exc:  # one unreadable book does not stop the others
                meta["skipped"][path.name] = f"{type(exc).__name__}: {exc}"[:300]
                say(f"{title(path)}：跳过（{exc}）")
                continue
            vecs = None
            if embed is not None and got:
                say(f"{title(path)}：{len(got)} 段，向量化中…")
                vecs = _normalized(embed([p.text for p in got]))
        if not got:
            meta["skipped"][path.name] = "没有读出文字（扫描版需要先做文字识别）"
            continue
        passages += got
        if vecs is not None:
            vectors.append(vecs)
        meta["books"][path.name] = {"title": title(path), "passages": len(got), "stamp": stamp,
                                    "pages": len({p.pdf_page for p in got if p.pdf_page})}
    out.mkdir(parents=True, exist_ok=True)
    with (out / "passages.jsonl").open("w", encoding="utf-8") as f:
        for p in passages:
            f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
    vector_file = out / "vectors.npy"
    if embed is not None and vectors and sum(len(v) for v in vectors) == len(passages):
        np.save(vector_file, np.concatenate(vectors))
        meta["embed"] = "BAAI/bge-small-zh-v1.5" if embedder == "auto" else "custom"
    elif vector_file.exists():
        vector_file.unlink()
    meta["passages"] = len(passages)
    meta["seconds"] = round(time.monotonic() - started, 1)
    meta["folder"] = str(folder)
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _CACHE.pop(out, None)
    return meta


def _meta(folder: Path) -> dict:
    try:
        data = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class RulesIndex:
    def __init__(self, passages: list[Passage], vectors=None, *, embedder: Embedder | None = None):
        self.passages = passages
        self.vectors = vectors
        self.embedder = embedder
        self._df: dict[str, int] = {}

    # Words: character bigrams of the Chinese, lowercased words of the rest. Rules questions are
    # short and their terms (溺水, 体质检定, 坠落) are what a passage about them says too.
    @staticmethod
    def words(text: str) -> list[str]:
        out = []
        for run in re.findall(r"[一-鿿]+|[A-Za-z]{3,}", text):
            if run.isascii():
                out.append(run.lower())
            else:
                out += [run[i:i + 2] for i in range(len(run) - 1)] or [run]
        return [w for w in dict.fromkeys(out) if not set(w) & STOP]

    def _idf(self, word: str) -> float:
        if word not in self._df:
            self._df[word] = sum(1 for p in self.passages if word in (p.text.lower() if word.isascii() else p.text))
        n = len(self.passages)
        return math.log((n + 1) / (self._df[word] + 0.5))

    def _word_scores(self, query: str) -> list[float]:
        # A word no passage has says nothing about which passage it is: it is left out of the total.
        weights = {w: idf for w in self.words(query) if (idf := self._idf(w)) and self._df[w]}
        total = sum(weights.values()) or 1.0
        scores = []
        for p in self.passages:
            text = p.text + " " + p.section + " " + p.chapter
            lowered = text.lower()
            scores.append(sum(wt for w, wt in weights.items() if w in (lowered if w.isascii() else text)) / total)
        return scores

    def search(self, query: str, k: int = HITS, *, floor: float = SCORE_FLOOR) -> list[Passage]:
        query = query.strip()
        if not query or not self.passages:
            return []
        words = self._word_scores(query)
        scores = words
        if self.vectors is not None and self.embedder is not None:
            q = _normalized(self.embedder([QUERY_PREFIX + query]))[0]
            similar = (self.vectors @ q).tolist()
            scores = [s + WORD_WEIGHT * w for s, w in zip(similar, words, strict=True)]
            floor = floor + 0.2  # a cosine between unrelated Chinese passages is already ~0.3
        ranked = sorted(range(len(self.passages)), key=lambda i: -scores[i])
        out, seen = [], set()
        for i in ranked:
            if scores[i] < floor or len(out) >= k:
                break
            p = self.passages[i]
            key = (p.file, p.pdf_page) if p.pdf_page else (p.file, p.chapter, p.section)
            if key not in seen:  # one passage a page (a heading, unpaged): its neighbours say the same
                seen.add(key)
                out.append(p)
        return out


def load(folder: Path | None = None, *, embedder: Embedder | str | None = "auto") -> RulesIndex | None:
    folder = folder or paths.rules_index()
    rows = folder / "passages.jsonl"
    if not rows.exists():
        return None
    passages = [Passage(**json.loads(line)) for line in rows.read_text(encoding="utf-8").splitlines() if line.strip()]
    vectors = None
    if (folder / "vectors.npy").exists():
        import numpy as np

        vectors = np.load(folder / "vectors.npy")
        if len(vectors) != len(passages):
            vectors = None
    embed = local_embedder() if embedder == "auto" and vectors is not None else (None if embedder == "auto" else embedder)
    return RulesIndex(passages, vectors, embedder=embed)  # type: ignore[arg-type]


_CACHE: dict[Path, tuple[float, RulesIndex | None]] = {}


def ready(folder: Path | None = None) -> bool:
    """Whether a rules index exists: without one nothing asks for a rule."""
    return ((folder or paths.rules_index()) / "passages.jsonl").exists()


def index(folder: Path | None = None) -> RulesIndex | None:
    """The index, loaded once per build (meta.json's time)."""
    folder = folder or paths.rules_index()
    meta = folder / "meta.json"
    stamp = meta.stat().st_mtime if meta.exists() else 0.0
    if folder not in _CACHE or _CACHE[folder][0] != stamp:
        _CACHE[folder] = (stamp, load(folder))
    return _CACHE[folder][1]


def lookup(query: str, k: int = HITS, folder: Path | None = None) -> list[Passage]:
    found = index(folder)
    return found.search(query, k) if found is not None else []


async def alookup(query: str, k: int = HITS, folder: Path | None = None) -> list[Passage]:
    """lookup() off the event loop: embedding the query and loading the index take a moment."""
    import asyncio

    return await asyncio.to_thread(lookup, query, k, folder)


def quote(passages: list[Passage]) -> str:
    """Passages as a prompt shows them: each with its book and page."""
    return "\n".join(f"- {p.cite()}：{p.text}" for p in passages)
