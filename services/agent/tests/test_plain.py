"""Plain-language display names (agent_service/plain.py): what farmers read instead of ids and codes."""

import datetime as dt

from agent_service import plain


def test_names_sources_units_and_periods_read_as_everyday_words():
    assert plain.name("fact.reference_et0", "uk") == "скільки води випаровується"
    assert plain.name("fact.something_new", "en") == "something new"            # unknown key: readable words
    assert plain.source("SPL4SMGP", "uk") == "супутник NASA SMAP"
    assert plain.source("HLSL30_VI", "en").startswith("NASA satellite images")
    assert plain.source("FAO-56", "en") == "FAO-56"                              # unknown product: its own id
    assert plain.unit("mm", "uk") == " мм" and plain.unit("m3/m3", "en") == " m³/m³" and plain.unit("ppm", "en") == " ppm"
    assert plain.period(dt.date(2025, 7, 1), dt.date(2025, 7, 31), "uk") == "01.07–31.07.2025"
    assert plain.period(dt.date(2024, 11, 1), dt.date(2025, 3, 31), "en") == "1 Nov 2024 – 31 Mar 2025"
    assert plain.crop("winter_wheat", "uk") == "озима пшениця" and plain.crop("teff", "en") == "teff"
    assert plain.rotation("rot_adaptive", "uk") == "Адаптивна сівозміна" and plain.rotation("rot_cover_mix", "en") == "Cover mix"
    assert [plain.unit("d", "uk", n) for n in (1, 3, 5, 11, 12, 21, 22, 290, 1.5)] == [
        " день", " дні", " днів", " днів", " днів", " день", " дні", " днів", " дня"]
    assert plain.unit("d", "en", 1) == " day" and plain.unit("d", "en", 290) == " days"
