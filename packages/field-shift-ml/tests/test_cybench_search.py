import csv
import json
from copy import deepcopy
from datetime import date, timedelta

import numpy as np
import pytest

from field_shift_ml import cybench_global as base
from field_shift_ml import cybench_search as search


def prepared_panel(tmp_path):
    path = tmp_path / "features.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["adm_id", "year", "group", "yield", *base.FEATURES])
        for year in range(2008, 2024):
            for state in range(1, 31):
                group = f"US-{state:02}"
                # New and previously opened test values cannot affect inner selection.
                value = 1000 if year >= 2018 else 2 + state / 10
                writer.writerow(
                    [
                        f"{group}-001",
                        year,
                        group,
                        value,
                        year,
                        *([float(state)] * (len(base.FEATURES) - 1)),
                    ]
                )
    base.write_json(
        path.with_suffix(".json"),
        {
            "protocol_sha256": base.canonical_hash(search.PROTOCOL),
            "implementation": search.identity(),
            "features_sha256": base.file_digest(path)[0],
        },
    )
    return search.load_panel(path)


def cheap_predict(candidate, panel, train, test, year):
    return np.full(int(test.sum()), np.mean(panel.y[train]))


def test_exact_predeclared_search_and_no_protocol_mutation(tmp_path):
    original = base.canonical_hash(base.PROTOCOL)
    assert len(search.CANDIDATES) == 22
    assert len(set(c["id"] for c in search.CANDIDATES)) == 22
    path = tmp_path / "protocol.json"
    assert search.freeze(path) == search.check_protocol(path)
    altered = json.loads(path.read_text())
    altered["protocol"]["outer_test_years"] = [2018]
    base.write_json(path, altered)
    with pytest.raises(ValueError, match="protocol"):
        search.check_protocol(path)
    with pytest.raises(ValueError, match="replace"):
        search.freeze(path)
    assert base.canonical_hash(base.PROTOCOL) == original


def test_every_inner_fold_excludes_opened_and_outer_targets_and_spatial_holdouts(
    tmp_path, monkeypatch
):
    panel = prepared_panel(tmp_path)
    called = []

    def probe(candidate, panel, train, test, year):
        assert year in [2014, 2015, 2016, 2017]
        assert max(panel.years[train]) == year - 3
        assert set(panel.years[test]) == {year}
        assert panel.y[train].max() < 1000
        assert panel.y[test].max() < 1000
        called.append((candidate["id"], year, train.copy(), test.copy()))
        return cheap_predict(candidate, panel, train, test, year)

    monkeypatch.setattr(search, "predict", probe)
    selection = search.tune(panel)
    assert len(called) == selection["inner_fit_count"] == 176
    for _, _, train, test in called[88:]:
        assert not (train & panel.held_out).any()
        assert not (test & panel.held_out).any()
    for track in selection["tracks"].values():
        assert track["selected_overall"] == "country_mean"  # exact tie uses declared order
        assert len(track["selected_per_family"]) == 6
    # Alter all 2018–2023 outcomes: selection bytes and rankings remain identical.
    panel.y[panel.years >= 2018] = 1e9
    assert search.tune(panel) == selection


def test_outer_evaluation_requires_persisted_selection_and_past_labels(tmp_path, monkeypatch):
    panel = prepared_panel(tmp_path)
    selection_path = tmp_path / "selection.json"
    monkeypatch.setattr(search, "predict", cheap_predict)
    selection = search.tune(panel)
    with pytest.raises(FileNotFoundError):
        search.evaluate(panel, selection_path, tmp_path / "out.csv")
    digest = search.save_selection(selection_path, selection)
    calls = []

    def probe(candidate, panel, train, test, year):
        assert selection_path.exists()
        assert json.loads(selection_path.read_text())["sha256"] == digest
        assert year in [2021, 2022, 2023]
        assert max(panel.years[train]) == year - 3
        assert not (train & (panel.years >= 2021)).any()
        calls.append(year)
        return cheap_predict(candidate, panel, train, test, year)

    monkeypatch.setattr(search, "predict", probe)
    result = search.evaluate(panel, selection_path, tmp_path / "out.csv")
    assert result["outer_fit_count"] == len(calls) == 36
    assert result["selection_sha256"] == digest
    for fold in result["folds"]:
        if fold["track"] == "spatial_chronological":
            assert set(fold["train_groups"]).isdisjoint(fold["test_groups"])
    assert json.loads(selection_path.read_text())["selection"] == selection


@pytest.mark.parametrize("failure", ["exception", "nonfinite"])
def test_failed_candidates_abort_complete_search(tmp_path, monkeypatch, failure):
    panel = prepared_panel(tmp_path)

    def fail(candidate, panel, train, test, year):
        if failure == "exception":
            raise RuntimeError("candidate failed")
        return np.full(test.sum(), np.nan)

    monkeypatch.setattr(search, "predict", fail)
    with pytest.raises((RuntimeError, ValueError)):
        search.tune(panel)
    assert not (tmp_path / "selection.json").exists()


def test_insufficient_fold_cannot_be_silently_skipped(tmp_path):
    panel = prepared_panel(tmp_path)
    panel.years[panel.years == 2014] = 2018
    with pytest.raises(ValueError, match="insufficient"):
        search.masks(panel, "chronological", 2014, inner=True)
    with pytest.raises(ValueError, match="outside"):
        search.masks(panel, "chronological", 2018, inner=True)


@pytest.mark.parametrize("kind", ["hash", "winner", "missing_candidate"])
def test_tampered_selection_is_rejected_even_if_self_rehashed(tmp_path, monkeypatch, kind):
    panel = prepared_panel(tmp_path)
    monkeypatch.setattr(search, "predict", cheap_predict)
    selection = search.tune(panel)
    path = tmp_path / "selection.json"
    search.save_selection(path, selection)
    wrapped = json.loads(path.read_text())
    if kind == "hash":
        wrapped["sha256"] = "wrong"
    else:
        track = wrapped["selection"]["tracks"]["chronological"]
        if kind == "winner":
            track["selected_overall"] = "ridge_a1"
        else:
            track["candidates"].pop()
        wrapped["sha256"] = base.canonical_hash(wrapped["selection"])
    base.write_json(path, wrapped)
    with pytest.raises(ValueError):
        search.load_selection(path, panel)


def test_feature_identity_and_finite_value_guards(tmp_path):
    prepared_panel(tmp_path)
    path = tmp_path / "features.csv"
    path.write_text(path.read_text().replace(",2.1,", ",nan,", 1))
    with pytest.raises(ValueError, match="checksum"):
        search.load_panel(path)
    metadata = json.loads(path.with_suffix(".json").read_text())
    metadata["features_sha256"] = base.file_digest(path)[0]
    base.write_json(path.with_suffix(".json"), metadata)
    with pytest.raises(ValueError, match="nonfinite"):
        search.load_panel(path)


def write_weather(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["adm_id", "date", *base.WEATHER])
        writer.writerows(rows)


def test_extended_weather_windows_respect_2023_cutoff_and_leap_days(tmp_path):
    rows = []
    start, end = date(2016, 1, 1), date(2020, 12, 31)
    while start <= end:
        rows.append(["US-01-001", start.strftime("%Y%m%d"), 0, 20, 10, 2, 3, 1, 5])
        start += timedelta(days=1)
    start, end = base.weather_window(2023)
    while start <= end:
        rows.append(["US-01-001", start.strftime("%Y%m%d"), 0, 20, 10, 2, 3, 1, 5])
        start += timedelta(days=1)
    rows.append(["US-01-001", "20211218", 0, 30, 15, 99999, 99999, 99999, 99999])
    path = tmp_path / "meteo.csv"
    write_weather(path, rows)
    annual, recent, _ = search.weather_counts(path, {"US-01-001"})
    values = base.make_weather_features("US-01-001", 2023, annual, recent)
    assert values[:7] == [0, 20, 10, 180, 270, 90, 5]
    assert values[10] == pytest.approx(2 * (365 * 5 + 2) / 5)
    rows.pop(-2)  # Missing the final allowed day must abstain.
    write_weather(path, rows)
    annual, recent, _ = search.weather_counts(path, {"US-01-001"})
    assert base.make_weather_features("US-01-001", 2023, annual, recent) is None


@pytest.mark.parametrize("kind", ["duplicate", "unordered", "bad_date"])
def test_malformed_weather_fails(tmp_path, kind):
    rows = [["US-01-001", "20210102", 0, 20, 10, 2, 3, 1, 5]]
    if kind == "duplicate":
        rows.append(rows[0])
    elif kind == "unordered":
        rows.append(["US-01-001", "20210101", 0, 20, 10, 2, 3, 1, 5])
    else:
        rows[0][1] = "2021012"
    path = tmp_path / "weather.csv"
    write_weather(path, rows)
    with pytest.raises(ValueError):
        search.weather_counts(path, {"US-01-001"})


def test_extended_labels_keep_zero_and_count_negative_and_missing(tmp_path):
    path = tmp_path / "labels.csv"
    path.write_text(
        "adm_id,harvest_year,yield\nUS-01-001,2021,0\nUS-01-001,2022,-1\n"
        "US-01-001,2023,nan\nUS-01-001,2020,2\nUS-01-001,2007,3\n"
    )
    rows, counts = search.labels(path)
    assert [(r["year"], r["yield"]) for r in rows] == [(2021, 0), (2020, 2)]
    assert counts["negative_or_nonfinite_label"] == 2
    assert counts["zero_yield"] == counts["outside_protocol_years"] == 1
    path.write_text(path.read_text() + "US-01-001,2021,0\n")
    with pytest.raises(ValueError, match="duplicate"):
        search.labels(path)


@pytest.mark.parametrize("family", search.FAMILIES)
def test_actual_candidate_families_run_on_small_inputs(tmp_path, family):
    panel = prepared_panel(tmp_path)
    candidate = deepcopy(next(c for c in search.CANDIDATES if c["family"] == family))
    train, test = search.masks(panel, "chronological", 2014, inner=True)
    with search.threadpool_limits(limits=2):
        predicted = search.predict(candidate, panel, train, test, 2014)
    assert predicted.shape == (30,)
    assert np.isfinite(predicted).all() and (predicted >= 0).all()


def test_timeout_is_failure_not_a_partial_selection():
    with pytest.raises(TimeoutError, match="incomplete"):
        search.timeout_handler(None, None)


def forbid_boundary_call(*_args, **_kwargs):
    raise AssertionError("path rejection must precede content reads, prediction and writes")


@pytest.mark.parametrize("alias", ["same", "dotdot", "symlink", "hardlink"])
def test_direct_evaluate_preserves_selection_when_prediction_aliases_it(
    tmp_path, monkeypatch, alias
):
    selection = tmp_path / "selection.json"
    original = b'{"immutable": "selection evidence"}\n'
    selection.write_bytes(original)
    if alias == "same":
        prediction = selection
    elif alias == "dotdot":
        (tmp_path / "sub").mkdir()
        prediction = tmp_path / "sub" / ".." / "selection.json"
    else:
        prediction = tmp_path / "predictions.csv"
        if alias == "symlink":
            prediction.symlink_to(selection)
        else:
            prediction.hardlink_to(selection)
    monkeypatch.setattr(search, "load_selection", forbid_boundary_call)
    monkeypatch.setattr(search, "predict", forbid_boundary_call)
    with pytest.raises(ValueError, match="artifact paths|already exists"):
        search.evaluate(None, selection, prediction)
    assert selection.read_bytes() == prediction.read_bytes() == original


def test_direct_evaluate_rejects_existing_unrelated_predictions_before_loading(
    tmp_path, monkeypatch
):
    prediction = tmp_path / "old.csv"
    prediction.write_text("existing prediction evidence")
    monkeypatch.setattr(search, "load_selection", forbid_boundary_call)
    with pytest.raises(ValueError, match="already exists"):
        search.evaluate(None, tmp_path / "selection.json", prediction)
    assert prediction.read_text() == "existing prediction evidence"


@pytest.mark.parametrize("existing", ["csv", "sidecar", "raw_csv", "raw_receipt", "dangling_link"])
def test_direct_prepare_rejects_existing_destinations_before_source_reads(
    tmp_path, monkeypatch, existing
):
    output = tmp_path / "prepared.csv"
    data_root = tmp_path / "data"
    if existing.startswith("raw_"):
        folder = data_root / "maize" / "US"
        folder.mkdir(parents=True)
        output = folder / (
            "yield_maize_US.csv" if existing == "raw_csv" else "yield_maize_US.manifest.json"
        )
    protected = output.with_suffix(".json") if existing == "sidecar" else output
    if existing == "dangling_link":
        protected.symlink_to(tmp_path / "missing")
    else:
        protected.write_text("original data or evidence")
    monkeypatch.setattr(base, "verify_member", forbid_boundary_call)
    with pytest.raises(ValueError, match="artifact paths|already exists"):
        search.prepare(data_root, output, "unused")
    if existing == "dangling_link":
        assert protected.is_symlink() and not protected.exists()
        assert not (tmp_path / "missing").exists()
    else:
        assert protected.read_text() == "original data or evidence"


def test_prepare_csv_json_sidecar_collision_fails_before_reads(tmp_path, monkeypatch):
    output = tmp_path / "prepared.json"
    monkeypatch.setattr(base, "verify_member", forbid_boundary_call)
    with pytest.raises(ValueError, match="artifact paths"):
        search.prepare(tmp_path / "data", output, "unused")
    assert not output.exists()


@pytest.mark.parametrize(
    "pair",
    [
        ("selection", "predictions"),
        ("selection", "output"),
        ("predictions", "output"),
        ("protocol", "selection"),
        ("protocol", "predictions"),
        ("protocol", "output"),
        ("prepared", "selection"),
        ("prepared", "predictions"),
        ("prepared", "output"),
        ("prepared_metadata", "selection"),
        ("prepared_metadata", "predictions"),
        ("prepared_metadata", "output"),
        ("protocol", "prepared"),
        ("protocol", "prepared_metadata"),
    ],
)
@pytest.mark.parametrize("alias", ["same", "dotdot", "symlink", "hardlink"])
def test_cli_run_rejects_all_artifact_alias_roles_before_reads(tmp_path, monkeypatch, pair, alias):
    paths = {
        name: tmp_path / f"{name}.json"
        for name in ("protocol", "selection", "predictions", "output")
    }
    paths["prepared"] = tmp_path / "prepared.csv"
    paths["prepared_metadata"] = paths["prepared"].with_suffix(".json")
    first, second = pair
    # The prepared sidecar is derived, so alias the other role to that fixed path.
    if second in {"prepared", "prepared_metadata"}:
        first, second = second, first
    target = paths[first]
    if alias in {"symlink", "hardlink"}:
        target.write_text("protected original")
        if alias == "symlink":
            paths[second].symlink_to(target)
        else:
            paths[second].hardlink_to(target)
    elif alias == "dotdot":
        (tmp_path / "sub").mkdir()
        paths[second] = tmp_path / "sub" / ".." / target.name
    else:
        paths[second] = target
    snapshots = {p: p.read_bytes() for p in set(paths.values()) if p.exists()}
    monkeypatch.setattr(search, "check_protocol", forbid_boundary_call)
    monkeypatch.setattr(search, "tune", forbid_boundary_call)
    monkeypatch.setattr(search, "predict", forbid_boundary_call)
    args = ["run"]
    for name in ("protocol", "prepared", "selection", "predictions", "output"):
        args.extend(["--" + name, str(paths[name])])
    with pytest.raises(ValueError, match="artifact paths|already exists"):
        search.main(args)
    assert all(p.read_bytes() == value for p, value in snapshots.items())
    assert all(not p.exists() for p in set(paths.values()) - set(snapshots))


def test_cli_prepare_protects_protocol_and_checks_before_content_reads(tmp_path, monkeypatch):
    protocol = tmp_path / "prepared.json"
    protocol.write_text("frozen protocol")
    monkeypatch.setattr(search, "check_protocol", forbid_boundary_call)
    with pytest.raises(ValueError, match="artifact paths|already exists"):
        search.main(
            [
                "prepare",
                "--protocol",
                str(protocol),
                "--prepared",
                str(tmp_path / "prepared.csv"),
                "--data-root",
                str(tmp_path / "data"),
            ]
        )
    assert protocol.read_text() == "frozen protocol"
    assert not (tmp_path / "prepared.csv").exists()


def test_missing_destinations_through_symlinked_parent_are_aliases(tmp_path, monkeypatch):
    directory = tmp_path / "real"
    directory.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    monkeypatch.setattr(search, "check_protocol", forbid_boundary_call)
    with pytest.raises(ValueError, match="artifact paths"):
        search.main(
            [
                "run",
                "--protocol",
                str(tmp_path / "protocol.json"),
                "--prepared",
                str(tmp_path / "prepared.csv"),
                "--selection",
                str(directory / "selection.json"),
                "--predictions",
                str(alias / "selection.json"),
                "--output",
                str(tmp_path / "report.json"),
            ]
        )
    assert list(directory.iterdir()) == []


def test_exclusive_json_writer_does_not_replace_a_file_created_after_validation(tmp_path):
    path = tmp_path / "result.json"
    search.validate_artifact_paths({"output": path}, new=("output",))
    path.write_text("created by another writer")
    with pytest.raises(FileExistsError):
        search.write_new_json(path, {"new": "result"})
    assert path.read_text() == "created by another writer"
