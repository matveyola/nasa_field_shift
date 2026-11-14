"""Structure-aware chunking of parsed FAO pages.

Rules:
- chunks never cross a heading; each carries chapter, section path, heading and anchor;
- an equation stays with the paragraph before it and a following 'where ...' explanation;
- tables are chunked by row group (e.g. one crop group of Table 12), each chunk repeating the
  caption and full column names on every row, so a value is never separated from its crop,
  column or unit; footnotes become their own 'table_notes' chunk;
- chunk ids are derived from content, so re-ingesting unchanged content gives the same ids and
  changed content gives new ids (old citations cannot silently point at new text).
"""

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import quote

from .parse import Block, Table, render_row
from .sources import PUBLICATIONS, SourcePage

TARGET_CHARS = 1100     # ~250-300 tokens: fits the reranker's 512-token window with the query
MAX_CHARS = 1800        # hard cap for prose (equation + explanation may need more than target)
TABLE_MAX_CHARS = 1600
CHUNKER_VERSION = "2"


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    publication_id: str
    chapter: str
    section: str          # heading path inside the chapter, " > " separated
    heading: str          # nearest heading
    anchor: str | None
    url: str              # page URL + #anchor of the nearest heading
    kind: str             # prose | table | table_notes | example
    text: str             # verbatim normalised source text: the only quotable content
    table: str | None = None
    table_complete: bool = True
    transcribed_images: list[str] = field(default_factory=list)
    ordinal: int = 0
    start: int = 0        # offsets of `text` inside the page's normalised document text
    end: int = 0
    language: str = "en"

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    def index_text(self, chapter_title: str) -> str:
        """Text used for lexical and dense indexing: heading context + content."""
        context = f"{chapter_title} > {self.section}" if self.section else chapter_title
        return f"{context}\n{self.text}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "section"


def _split_sentences(text: str, limit: int) -> list[str]:
    parts, current = [], ""
    for sentence in re.split(r"(?<=[.;:])\s+(?=[A-Z(])", text):
        if current and len(current) + 1 + len(sentence) > limit:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


def table_text_lines(table: Table, rows) -> list[str]:
    lines, group = [], None
    for row in rows:
        if row.group and row.group != group and not row.is_group:
            lines.append(f"Group: {row.group}")
        group = row.group
        lines.append(render_row(table, row))
    return lines


def chunk_page(source: SourcePage, blocks: list[Block]) -> tuple[str, list[Chunk]]:
    """Return the page's normalised document text and its chunks."""
    publication = PUBLICATIONS[source.publication_id]
    doc_parts: list[str] = []
    offsets: list[tuple[int, int]] = []
    position = 0
    for block in blocks:
        text = block_document_text(block)
        doc_parts.append(text)
        offsets.append((position, position + len(text)))
        position += len(text) + 2
    doc_text = "\n\n".join(doc_parts)

    chunks: list[Chunk] = []
    path: dict[int, str] = {}
    anchor: str | None = None
    buffer: list[int] = []  # indices of prose blocks waiting to be emitted

    def section() -> str:
        return " > ".join(path[k] for k in sorted(path) if k >= 2)

    def heading() -> str:
        return path[max(path)] if path else source.title

    def url() -> str:
        return source.url + (f"#{quote(anchor, safe='=')}" if anchor else "")  # "#page=12" opens PDFs at the page

    def make(kind: str, text: str, span: tuple[int, int], **kw) -> None:
        chunks.append(Chunk(
            chunk_id="", doc_id=source.id, publication_id=publication.id, chapter=source.chapter,
            section=section(), heading=heading(), anchor=anchor, url=url(), kind=kind, text=text,
            start=span[0], end=span[1], language=publication.language, **kw))

    def flush() -> None:
        if not buffer:
            return
        texts = [blocks[i].text for i in buffer]
        start, end = offsets[buffer[0]][0], offsets[buffer[-1]][1]
        joined = "\n\n".join(texts)
        transcribed = sorted({img for i in buffer for img in blocks[i].transcribed})
        if len(joined) <= MAX_CHARS:
            make("prose", joined, (start, end), transcribed_images=transcribed)
        else:  # a single very long paragraph: split on sentences (spans stay inside the block)
            for piece in _split_sentences(joined, TARGET_CHARS):
                at = doc_text.find(piece, start)
                make("prose", piece, (at, at + len(piece)) if at >= 0 else (start, end), transcribed_images=transcribed)
        buffer.clear()

    for i, block in enumerate(blocks):
        if block.kind == "heading":
            flush()
            path = {k: v for k, v in path.items() if k < block.level}
            path[block.level] = block.text
            anchor = block.anchor or anchor
            continue
        if block.kind == "figure":
            continue
        if block.anchor and block.anchor != anchor and block.anchor.startswith("page="):
            flush()  # PDF paragraphs carry their page: a chunk never spans two pages
            anchor = block.anchor
        if block.kind == "table" and block.table:
            flush()
            _table_chunks(block.table, offsets[i], make, block.transcribed)
            continue
        if block.kind == "example":
            flush()
            for piece in _split_sentences(block.text, TABLE_MAX_CHARS):
                at = doc_text.find(piece, offsets[i][0])
                make("example", piece, (at, at + len(piece)) if at >= 0 else offsets[i], transcribed_images=block.transcribed)
            continue
        # Prose: equations and their 'where' explanations stay with the preceding paragraph.
        size = sum(len(blocks[j].text) + 2 for j in buffer)
        glued = block.kind == "equation" or block.text.lower().startswith(("where", "with"))
        if buffer and not glued and size + len(block.text) > TARGET_CHARS:
            flush()
        buffer.append(i)
    flush()

    seen: dict[str, int] = {}
    for n, chunk in enumerate(chunks):
        base = f"{chunk.doc_id}.{_slug(chunk.anchor or chunk.heading)}.{chunk.content_hash[:10]}"
        seen[base] = seen.get(base, 0) + 1
        chunk.chunk_id = base if seen[base] == 1 else f"{base}-{seen[base]}"
        chunk.ordinal = n
    return doc_text, chunks


def block_document_text(block: Block) -> str:
    if block.kind == "table" and block.table:
        t = block.table
        lines = [t.caption] + table_text_lines(t, t.rows) + (["Notes:"] + t.notes if t.notes else [])
        return "\n".join(lines)
    return block.text


def _table_chunks(table: Table, span: tuple[int, int], make, transcribed: list[str]) -> None:
    label = f"Table {table.number}" if table.number else None
    header = table.caption + ("\n[Extraction warning: " + "; ".join(table.problems) + "]" if table.problems else "")
    groups: list[list] = []
    for row in table.rows:
        if row.is_group or not groups or (groups[-1] and groups[-1][-1].group != row.group):
            groups.append([])
        groups[-1].append(row)
    pieces: list[list] = []
    for group_rows in groups:
        current: list = []
        for row in group_rows:
            trial = "\n".join(table_text_lines(table, current + [row]))
            if current and len(header) + len(trial) > TABLE_MAX_CHARS:
                pieces.append(current)
                current = []
            current.append(row)
        if current:
            # Merge small consecutive groups (e.g. 2-row tables) into one chunk when they fit.
            if pieces and len(header) + len("\n".join(table_text_lines(table, pieces[-1] + current))) <= TARGET_CHARS \
                    and not any(r.is_group for r in current):
                pieces[-1] += current
            else:
                pieces.append(current)
    for rows in pieces:
        text = header + "\n" + "\n".join(table_text_lines(table, rows))
        make("table", text, span, table=label, table_complete=table.complete, transcribed_images=transcribed)
    if table.notes:
        notes = table.caption + "\nNotes:\n" + "\n".join(table.notes)
        for piece in _split_sentences(notes, TABLE_MAX_CHARS) if len(notes) > TABLE_MAX_CHARS else [notes]:
            make("table_notes", piece, span, table=label, table_complete=table.complete, transcribed_images=transcribed)
