"""Source registry: the only documents the knowledge base may contain.

Nothing outside SOURCES is ever fetched. Adding a source means adding it here, reviewing its
usage terms, and rebuilding the index.
"""

from dataclasses import dataclass

FAO_PERMISSIONS_URL = "https://www.fao.org/publications/about-fao-publishing/permissions/en"


@dataclass(frozen=True)
class Publication:
    id: str
    title: str
    authors: str
    publisher: str
    year: int
    series: str
    canonical_url: str
    language: str
    licence: str
    citation_licence: str = ""  # the short form carried by every Citation (<= 300 chars)


@dataclass(frozen=True)
class SourcePage:
    id: str               # stable document id used in chunk ids and citations
    publication_id: str
    chapter: str
    title: str
    url: str              # exact allowlisted URL
    fmt: str = "html"     # html | pdf


FAO56 = Publication(
    id="fao56",
    title="Crop evapotranspiration - Guidelines for computing crop water requirements",
    authors="Allen, R.G., Pereira, L.S., Raes, D., Smith, M.",
    publisher="FAO",
    year=1998,
    series="FAO Irrigation and Drainage Paper 56",
    canonical_url="https://www.fao.org/4/x0490e/x0490e00.htm",
    language="en",
    # Summary of the FAO permissions page (read 2026-11-06). Not legal advice.
    licence=("© FAO 1998. Use for private study, research, teaching and non-commercial products with "
             "acknowledgement of FAO as source and copyright holder; FAO endorsement must not be stated or "
             "implied. Other reuse needs an FAO licence request: " + FAO_PERMISSIONS_URL),
)

# Ukrainian regional recommendations of NAAS research institutes (added 2026-11-10). Text PDFs; the title, issuer
# and page numbers were read in the documents themselves. Not added: the 2019 Southern Steppe booklet of the
# Institute of Irrigated Agriculture, whose PDF text falls apart into single characters ("14,0" as "1", "4,0"), so
# its numbers could not be quoted reliably.
NAAS_LICENCE = ("Науково-практичні рекомендації установи НААН у відкритому доступі; умови повторного використання "
                "в документі не вказані. Цитуються коротко, з назвою установи та сторінкою, у некомерційному прототипі.")
NAAS_CITATION_LICENCE = "Рекомендації установи НААН у відкритому доступі; коротка цитата з посиланням на джерело."

NAAS_CHERKASY_2024 = Publication(
    id="naas-cherkasy-2024",
    title="Проведення весняно-польових робіт в АПК Черкаської області в умовах 2024 року",
    authors="Черкаська державна сільськогосподарська дослідна станція ННЦ «Інститут землеробства НААН»",
    publisher="Черкаська ДСГДС ННЦ «Інститут землеробства НААН»",
    year=2024,
    series="науково-практичні рекомендації; Лісостеп, Черкаська обл.",
    canonical_url="https://agrobiz.net/modules/files/file/rekomendatsii-vesna-2024-1-517526.pdf",
    language="uk", licence=NAAS_LICENCE, citation_licence=NAAS_CITATION_LICENCE,
)
NAAS_POLISSIA_2026 = Publication(
    id="naas-polissia-2026",
    title="Підготовка та проведення посіву озимих сільськогосподарських культур в агроформуваннях Житомирської "
          "області в умовах 2026 року",
    authors="Рижук С.М., Савчук О.І., Приймачук Т.Ю., Штанько Т.А., Меша К.В., Ратошнюк В.І. та ін.",
    publisher="Інститут сільського господарства Полісся НААН",
    year=2026,
    series="науково-практичні рекомендації; Полісся, Житомирська обл.",
    canonical_url="https://www.isgpnaan.org/upload/rekomendatsii-posiv-2026.pdf",
    language="uk", licence=NAAS_LICENCE, citation_licence=NAAS_CITATION_LICENCE,
)

PUBLICATIONS = {p.id: p for p in (FAO56, NAAS_CHERKASY_2024, NAAS_POLISSIA_2026)}

_BASE = "https://www.fao.org/4/x0490e/"
SOURCES: tuple[SourcePage, ...] = (
    SourcePage("fao56-ch01", "fao56", "1", "Chapter 1 - Introduction to evapotranspiration", _BASE + "x0490e04.htm"),
    SourcePage("fao56-ch05", "fao56", "5", "Chapter 5 - Introduction to crop evapotranspiration (ETc)", _BASE + "x0490e0a.htm"),
    SourcePage("fao56-ch06", "fao56", "6", "Chapter 6 - ETc - Single crop coefficient (Kc)", _BASE + "x0490e0b.htm"),
    SourcePage("fao56-ch07", "fao56", "7", "Chapter 7 - ETc - Dual crop coefficient (Kc = Kcb + Ke)", _BASE + "x0490e0c.htm"),
    SourcePage("fao56-ch08", "fao56", "8", "Chapter 8 - ETc under soil water stress conditions", _BASE + "x0490e0e.htm"),
    SourcePage("naas-cherkasy-2024", "naas-cherkasy-2024", "", "Весняно-польові роботи, Черкаська обл., 2024",
               NAAS_CHERKASY_2024.canonical_url, fmt="pdf"),
    SourcePage("naas-polissia-2026", "naas-polissia-2026", "", "Посів озимих, Житомирська обл., 2026",
               NAAS_POLISSIA_2026.canonical_url, fmt="pdf"),
)
SOURCES_BY_ID = {s.id: s for s in SOURCES}
ALLOWED_URLS = frozenset(s.url for s in SOURCES)

# Images that carry text the parser cannot read. Each one was opened and read by a person
# (2026-11-06); every other image becomes a visible placeholder. Transcriptions are marked as
# such in chunk text: they are not FAO's own characters.
VERIFIED_IMAGE_TEXT: dict[str, str] = {
    "x0490e2a.gif": "Kc ini^1",
    "x0490e2n.gif": "Kcb ini^1",
    "x0490e2o.gif": "Kcb mid",
    "x0490e2p.gif": "Kcb end",
    "x0490e2b.gif": "Kc ini = Kc ini (Fig 29) + (I - 10) / (40 - 10) [Kc ini (Fig 30) - Kc ini (Fig 29)]",
    "x0490e2g.gif": "Kc mid = 1.20 + [0.04 (1.3 - 2) - 0.004 (75 - 45)] (2/3)^0.3 = 1.07",
    "x0490e2h.gif": "Kc mid = 1.20 + [0.04 (4.6 - 2) - 0.004 (44 - 45)] (2/3)^0.3 = 1.30",
    "x0490e2j.gif": "Kc mid = 1.15 + [0.04 (2.2 - 2) - 0.004 (30 - 45)] (0.4/3)^0.3 = 1.19",
    "x0490e2t.gif": "Kc max = max({1.2 + [0.04 (3 - 2) - 0.004 (20 - 45)] (1/3)^0.3}, {0.9 + 0.05}) = 1.30",
    "x0490e32.gif": "Ks = (1 - b / (Ky 100) (ECe - ECe threshold)) = (1 - 19 / (1.15 (100)) (1.5 - 1.0)) = 0.92",
    # Transcribed exactly as printed, including the source's denominator (100 - 44) next to "66".
    "x0490e33.gif": "Ks = (TAW - Dr) / (TAW - RAW) = (110 - Dr) / (100 - 44) = (110 - Dr) / 66",
    "x0490e34.gif": "Ks = (1 - b / (Ky 100) (ECe - ECe threshold)) ((TAW - Dr) / (TAW - RAW)) = 0.92 ((110 - Dr) / 66)",
    # Numbered equations (read 2026-11-06). The images spell "treshold"; written "threshold" here.
    "x0490e2c.gif": "Iw = I / fw",
    "x0490e2d.gif": "Kc mid = Kc mid (Tab) + [0.04 (u2 - 2) - 0.004 (RHmin - 45)] (h/3)^0.3",
    "x0490e2e.gif": "RHmin = e°(Tdew) / e°(Tmax) 100",
    "x0490e2f.gif": "RHmin = e°(Tmin) / e°(Tmax) 100",
    "x0490e2i.gif": "Kc end = Kc end (Tab) + [0.04 (u2 - 2) - 0.004 (RHmin - 45)] (h/3)^0.3",
    "x0490e2k.gif": "Kc i = Kc prev + [(i - Σ(L prev)) / L stage] (Kc next - Kc prev)",
    "x0490e2l.gif": "K ratio = 1.2 + [0.04 (2.2 - 2) - 0.004 (30 - 45)] (0.5/3)^0.3 = 1.24",
    "x0490e2m.gif": "Kcb = Kcb (Tab) + [0.04 (u2 - 2) - 0.004 (RHmin - 45)] (h/3)^0.3",
    "x0490e2q.gif": "Kc max = max({1.2 + [0.04 (u2 - 2) - 0.004 (RHmin - 45)] (h/3)^0.3}, {Kcb + 0.05})",
    "x0490e2r.gif": "Kr = (TEW - De,i-1) / (TEW - REW) for De,i-1 > REW",
    "x0490e2s.gif": "fc = ((Kcb - Kc min) / (Kc max - Kc min))^(1 + 0.5h)",
    "x0490e2u.gif": "De,i = De,i-1 - (Pi - ROi) - Ii / fw + Ei / few + Tew,i + DPe,i",
    "x0490e2v.gif": "DPe,i = (Pi - ROi) + Ii / fw - De,i-1 ≥ 0",
    "x0490e2w.gif": "Ks = (TAW - Dr) / (TAW - RAW) = (TAW - Dr) / ((1 - p) TAW)",
    "x0490e2x.gif": "Ya / Ym = 1 - (ECe - ECe threshold) b / 100",
    "x0490e2y.gif": "(1 - Ya / Ym) = Ky (1 - ETc adj / ETc)",
    "x0490e2z.gif": "Ks = 1 - b / (Ky 100) (ECe - ECe threshold)",
    "x0490e30.gif": "Ks = (1 - b / (Ky 100) (ECe - ECe threshold)) ((TAW - Dr) / (TAW - RAW))",
    "x0490e31.gif": "ECe = (1 + LF) / LF · ECiw / 5",
}
# Figures (charts) embedded in layout tables: never data tables.
FIGURE_IMAGES = frozenset({"x0490e6m.gif", "x0490e6p.gif", "x0490e6q.gif", "x0490e6t.gif", "x0490e6u.gif"})
