"""Knowledge pipeline on the real FAO-56 pages (fetched once into data/knowledge/raw, then offline)."""

import io
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from agent_service.knowledge.chunk import chunk_page
from agent_service.knowledge.fetch import FetchError, fetch_bytes
from agent_service.knowledge.index import IndexBuildError, build_index, chunks_of
from agent_service.knowledge.parse import parse_page, render_row
from agent_service.knowledge.retrieve import KnowledgeBase, KnowledgeUnavailable, rrf
from agent_service.knowledge.sources import PUBLICATIONS, SOURCES, SOURCES_BY_ID, VERIFIED_IMAGE_TEXT

ROOT = Path(__file__).parents[1]
RAW = ROOT / "data" / "knowledge" / "raw"
needs_corpus = pytest.mark.skipif(not all((RAW / f"{s.id}.{s.fmt}").exists() for s in SOURCES),
                                  reason="FAO pages not fetched: uv run python -m agent_service.knowledge fetch")


def parsed(doc_id: str):
    source = SOURCES_BY_ID[doc_id]
    return parse_page((RAW / f"{doc_id}.html").read_bytes())[1], source


def table(doc_id: str, number: str):
    blocks, _ = parsed(doc_id)
    return next(b.table for b in blocks if b.kind == "table" and b.table.number == number)


@pytest.fixture(scope="module")
def lexical_index(tmp_path_factory):
    if not all((RAW / f"{s.id}.{s.fmt}").exists() for s in SOURCES):
        pytest.skip("FAO pages not fetched")
    raw = tmp_path_factory.mktemp("raw")
    for f in RAW.iterdir():
        shutil.copy(f, raw / f.name)
    path = tmp_path_factory.mktemp("index") / "knowledge.sqlite"
    build_index(raw, path, embed=False)
    return raw, path


# ------------------------------------------------------------------ registry and fetching


def test_only_allowlisted_urls_can_be_fetched():
    for url in ("https://www.fao.org/4/x0490e/x0490e0f.htm", "https://evil.example/x0490e0b.htm",
                "file:///etc/passwd", "http://169.254.169.254/latest/meta-data/"):
        with pytest.raises(FetchError, match="not allowlisted"):
            fetch_bytes(url)


def test_registry_ids_are_unique_and_valid():
    assert len({s.id for s in SOURCES}) == len(SOURCES)
    # FAO-56 pages, plus each NAAS booklet's own PDF (its publication's canonical URL), all over https
    assert all(s.url.startswith("https://www.fao.org/4/x0490e/") if s.fmt == "html"
               else s.fmt == "pdf" and s.url == PUBLICATIONS[s.publication_id].canonical_url and s.url.startswith("https://")
               for s in SOURCES)


# ------------------------------------------------------------------ parsing real pages


@needs_corpus
def test_table_12_keeps_crop_group_and_columns_together():
    t = table("fao56-ch06", "12")
    assert t.complete and [c for c in t.columns if "Kc" in c] == ["Kc ini^1", "Kc mid", "Kc end"]
    maize = next(r for r in t.rows if r.cells[0].startswith("Maize, Field"))
    line = render_row(t, maize)
    assert "Kc mid: 1.20" in line and maize.group == "i. Cereals"
    sugar_beet = render_row(t, next(r for r in t.rows if r.cells[0] == "Sugar Beet"))
    assert "Kc ini^1: 0.35 | Kc mid: 1.20 | Kc end: 0.70^5" in sugar_beet


@needs_corpus
def test_image_header_is_transcribed_only_from_the_verified_list():
    t = table("fao56-ch06", "12")
    assert "Kc ini^1" in t.columns  # x0490e2a.gif, read by a person
    assert VERIFIED_IMAGE_TEXT["x0490e2a.gif"] == "Kc ini^1"


@needs_corpus
def test_table_footnotes_stay_with_their_table_including_embedded_equation():
    notes = table("fao56-ch08", "22").notes
    assert [n[:2] for n in notes if n.startswith("^")] == ["^1", "^2", "^3", "^4", "^5"]
    assert any(n.startswith("p = p table 22 + 0.04 (5 - ETc)") for n in notes)


@needs_corpus
def test_units_row_is_part_of_the_header_and_values_are_rows():
    t = table("fao56-ch07", "19")
    assert any("θ FC / m^3/m^3" in c for c in t.columns)
    assert t.rows[0].cells[0] == "Sand"


@needs_corpus
def test_equations_stay_with_their_explanation():
    chunks = chunk_page(SOURCES_BY_ID["fao56-ch08"], parsed("fao56-ch08")[0])[1]
    taw = next(c for c in chunks if "TAW = 1000(θ FC - θ WP) Zr (82)" in c.text)
    assert "TAW the total available soil water in the root zone" in taw.text


@needs_corpus
def test_nothing_substantial_is_dropped():
    import re
    from collections import Counter

    from bs4 import BeautifulSoup

    from agent_service.knowledge.parse import decode

    for source in (s for s in SOURCES if s.fmt == "html"):  # PDFs: test_pdf_sources_keep_their_text_and_pages
        raw = (RAW / f"{source.id}.html").read_bytes()
        chunks = chunk_page(source, parse_page(raw)[1])[1]
        soup = BeautifulSoup(decode(raw), "lxml")
        for a in soup.find_all("a", href=True):
            a.decompose()  # in-page link lists are navigation
        visible = Counter(re.findall(r"[a-z]{4,}", soup.body.get_text(" ").lower()))
        kept = Counter(re.findall(r"[a-z]{4,}", " ".join(c.text for c in chunks).lower()))
        coverage = 1 - sum((visible - kept).values()) / sum(visible.values())
        assert coverage > 0.97, f"{source.id}: only {coverage:.1%} of the page text reached the chunks"


@needs_corpus
def test_chunks_carry_locators_and_bounded_size():
    for source in (s for s in SOURCES if s.fmt == "html"):
        _, chunks = chunk_page(source, parsed(source.id)[0])
        for c in chunks:
            assert c.url.startswith(source.url) and c.chapter == source.chapter and c.doc_id == source.id
            assert len(c.text) <= 2600, (c.chunk_id, len(c.text))
            if c.kind == "table":
                assert c.table and c.text.startswith("TABLE ")
                # The locator a citation shows must be the table the passage really is.
                assert c.text.startswith(c.table.upper() + "."), (c.chunk_id, c.table, c.text[:40])


@needs_corpus
def test_table_11_is_stage_lengths_and_table_12_is_crop_coefficients():
    """Regression for an M3 report error that called Table 11 rows "crop coefficients"."""
    _, chunks = chunk_page(SOURCES_BY_ID["fao56-ch06"], parsed("fao56-ch06")[0])
    captions = {c.table: c.text.split("\n", 1)[0] for c in chunks if c.kind == "table"}
    assert "Lengths of crop development stages" in captions["Table 11"]
    assert "crop coefficients, Kc" in captions["Table 12"]


# ------------------------------------------------------------------ index build


@needs_corpus
def test_reingesting_unchanged_content_keeps_chunk_ids(lexical_index, tmp_path):
    raw, path = lexical_index
    second = tmp_path / "again.sqlite"
    build_index(raw, second, embed=False)
    assert [c.chunk_id for c in chunks_of(path)] == [c.chunk_id for c in chunks_of(second)]


@needs_corpus
def test_changed_content_changes_only_affected_ids(lexical_index, tmp_path):
    raw, path = lexical_index
    changed = tmp_path / "raw"
    shutil.copytree(raw, changed)
    page = changed / "fao56-ch08.html"
    page.write_bytes(page.read_bytes().replace(b"Field capacity is the amount", b"Field capacity is the quantity"))
    (changed / "fetch_manifest.json").unlink()  # the edited copy no longer matches the recorded checksum
    out = tmp_path / "changed.sqlite"
    build_index(changed, out, embed=False)
    before = {c.chunk_id for c in chunks_of(path)}
    after = {c.chunk_id for c in chunks_of(out)}
    assert len(before - after) == 1 and len(after - before) == 1  # one chunk replaced; nothing else moved


@needs_corpus
def test_checksum_mismatch_with_manifest_fails_the_build(lexical_index, tmp_path):
    raw, _ = lexical_index
    corrupted = tmp_path / "raw"
    shutil.copytree(raw, corrupted)
    (corrupted / "fao56-ch05.html").write_bytes(b"<html><body>corrupted</body></html>")
    with pytest.raises(IndexBuildError, match="checksum"):
        build_index(corrupted, tmp_path / "x.sqlite", embed=False)


@needs_corpus
def test_malformed_page_fails_explicitly(lexical_index, tmp_path):
    raw, _ = lexical_index
    broken = tmp_path / "raw"
    shutil.copytree(raw, broken)
    (broken / "fao56-ch05.html").write_bytes(b"<html><body><p>no headings at all</p></body></html>")
    (broken / "fetch_manifest.json").unlink()
    with pytest.raises(IndexBuildError, match="no headings"):
        build_index(broken, tmp_path / "x.sqlite", embed=False)


def test_missing_index_is_reported():
    with pytest.raises(KnowledgeUnavailable, match="not found"):
        KnowledgeBase(Path("does/not/exist.sqlite"))


@needs_corpus
def test_stale_index_is_refused(lexical_index, tmp_path):
    _, path = lexical_index
    stale = tmp_path / "stale.sqlite"
    shutil.copy(path, stale)
    with closing(sqlite3.connect(stale)) as db:
        sources = json.loads(db.execute("SELECT value FROM meta WHERE key='sources'").fetchone()[0])
        sources["fao56-ch99"] = {"url": "x", "sha256": "y", "retrieved_at": None}
        db.execute("UPDATE meta SET value=? WHERE key='sources'", (json.dumps(sources),))
        db.commit()
    with pytest.raises(KnowledgeUnavailable, match="stale"):
        KnowledgeBase(stale)


# ------------------------------------------------------------------ retrieval


@needs_corpus
def test_lexical_only_is_labelled_and_finds_english_and_ukrainian(lexical_index):
    _, path = lexical_index
    kb = KnowledgeBase(path, mode="hybrid_rerank", load_models=False)
    assert kb.mode == "lexical" and kb.status()["lexical_only"] is True  # no embeddings in this index
    en = kb.search("RAW readily available water p TAW", k=5)
    assert en.mode == "lexical" and en.degraded  # asked for hybrid_rerank, told it ran lexical
    assert all(h.chunk.heading == "Readily available water (RAW)" for h in en.hits[:3])
    # Ukrainian reaches the English index only through the rewrite; ranking quality is measured by
    # the retrieval benchmark (evals/retrieval), not asserted here.
    uk = kb.search("Що таке польова вологоємність ґрунту?", k=5)
    assert {"field", "capacity", "soil"} <= set(uk.lexical_terms) and uk.hits


@needs_corpus
@pytest.mark.parametrize("hostile", ['" OR 1=1 --', "NEAR(a b)", "kc* AND ^mid", "'; DROP TABLE chunks; --", "\x00\x01", ""])
def test_hostile_queries_are_plain_text_not_fts_syntax(lexical_index, hostile):
    _, path = lexical_index
    kb = KnowledgeBase(path, mode="lexical")
    kb.search(hostile, k=3)  # must not raise
    assert len(kb.chunks) == len(chunks_of(path))


@needs_corpus
def test_topic_and_content_filters(lexical_index):
    _, path = lexical_index
    kb = KnowledgeBase(path, mode="lexical")
    result = kb.search("maize", k=8, topic="water_stress", content="tables")
    assert result.hits and all(h.chunk.chapter == "8" and h.chunk.kind in ("table", "table_notes") for h in result.hits)


def test_rrf_uses_ranks_not_raw_scores():
    fused = dict(rrf(["a", "b", "c"], ["c", "a"]))
    assert fused["a"] > fused["c"] > fused["b"]


@needs_corpus
def test_benchmark_gold_anchors_exist_in_the_corpus(lexical_index):
    from evals.retrieval.run import BENCHMARK, resolve_gold

    _, path = lexical_index
    kb = KnowledgeBase(path, mode="lexical")
    cases = json.loads(BENCHMARK.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 40 and {c["lang"] for c in cases} == {"en", "uk"}
    for case in cases:
        anchors = resolve_gold(kb, case)
        assert all(anchors), f"{case['id']}: an anchor matches no chunk"
        assert case["answerable"] == bool(case["gold"])
        joined = " ".join(c.text for c in kb.chunks.values())
        assert case["q"] not in joined  # no benchmark question is inside the searchable documents


@needs_corpus
def test_pdf_sources_keep_their_text_and_pages():
    """NAAS booklets: every chunk stays on one page, links to it, and is the page's own text; little is dropped."""
    import re
    from collections import Counter

    from pypdf import PdfReader

    from agent_service.knowledge.parse_pdf import _BULLETS, parse_pdf

    def norm(t: str) -> str:  # line breaks, spaces and hyphens differ between the page and the joined paragraphs
        return re.sub(r"[\s\-–]+", "", t.translate(_BULLETS))  # the parser's own bullet mapping

    def page_text(p) -> str:  # the parser drops lines that are only a short number (page numbers, chart axis labels)
        return "\n".join(ln for ln in (p.extract_text() or "").splitlines() if not re.fullmatch(r"\s*\d{1,3}\s*", ln))

    for source in (s for s in SOURCES if s.fmt == "pdf"):
        raw = (RAW / f"{source.id}.pdf").read_bytes()
        pages = [page_text(p) for p in PdfReader(io.BytesIO(raw)).pages]
        _, chunks = chunk_page(source, parse_pdf(raw))
        assert len(chunks) > 30
        for c in chunks:
            page = int(c.anchor.removeprefix("page="))
            assert c.url == f"{source.url}#page={page}" and c.language == "uk"
            assert norm(c.text) in norm(pages[page - 1]), (c.chunk_id, c.text[:60])
        words = lambda t: Counter(re.findall(r"[а-яіїєґ]{5,}", t.lower()))  # noqa: E731
        # headings are kept as each chunk's section/heading (its locator), not repeated in its text
        visible, kept = words(" ".join(pages)), words(" ".join(f"{c.section} {c.heading} {c.text}" for c in chunks))
        assert 1 - sum((visible - kept).values()) / sum(visible.values()) > 0.95, source.id
    soy = [c for c in chunk_page(SOURCES_BY_ID["naas-cherkasy-2024"], parse_pdf((RAW / "naas-cherkasy-2024.pdf").read_bytes()))[1]
           if "Сою починають сіяти за температури ґрунту" in c.text]
    assert soy and soy[0].anchor == "page=22"


@needs_corpus
def test_pdf_passages_name_their_crop():
    """Regression (2026-11-10): a triticale passage was cited for winter wheat because its section path had lost the
    crop. Every passage keeps the chapter and crop headings above it."""
    from agent_service.knowledge.parse_pdf import parse_pdf

    source = SOURCES_BY_ID["naas-polissia-2026"]
    chunks = chunk_page(source, parse_pdf((RAW / "naas-polissia-2026.pdf").read_bytes()))[1]
    on = lambda page: {c.section for c in chunks if c.anchor == f"page={page}"}  # noqa: E731
    assert all("ТРИТИКАЛЕ" in s for s in on(21) if "Строки" in s)
    assert all("ПШЕНИЦІ" in s for s in on(12))
    assert all("ЯЧМЕНЮ" in s for s in on(17)) and all("РІПАКУ" in s for s in on(25))
