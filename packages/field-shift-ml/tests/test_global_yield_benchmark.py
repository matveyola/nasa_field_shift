from __future__ import annotations

from copy import deepcopy

import pytest

from field_shift_ml import global_yield_benchmark as bench


def panel():
    rows = []
    for year in range(2011, 2016):
        for site, state in [("A", "WI"), ("B", "IL"), ("KELLOGG", "MI")]:
            for crop, target in [("maize", 8), ("soybean", 3), ("wheat", 4)]:
                for lead in ("early", "standard"):
                    rows.append(
                        dict(
                            id=f"{site}|{year}|{crop}",
                            site=site,
                            physical_site=site,
                            state=state,
                            year=year,
                            crop=crop,
                            lead=lead,
                            yield_t_ha=target + (year - 2011) * 0.1,
                            label_available_on=f"{year + 1}-06-01",
                            days_before_sowing=50,
                            features=dict(crop=crop, year=float(year), latitude=40.0),
                        )
                    )
    return rows


def test_chronological_splits_and_spatial_group_disjointness():
    rows = panel()
    # A second experiment alias of a held-out physical site must also remain excluded.
    alias = deepcopy(rows[0])
    alias.update(id="alias", site="A.two", state="OTHER")
    rows.append(alias)
    splits = bench.split_panel(rows, "unseen_state_WI", "early", "maize")
    assert {r["year"] for r in splits["fit"]} == {2011, 2012}
    assert {r["year"] for r in splits["select"]} == {2013}
    assert {r["year"] for r in splits["test"]} == {2015}
    assert all(r["physical_site"] != "A" for r in splits["fit"] + splits["select"])
    assert all(r["physical_site"] == "A" for r in splits["test"])
    with pytest.raises(ValueError, match="Duplicate"):
        bench.split_panel(rows + [rows[1]], "temporal", "standard", "maize")


def test_metrics_bias_r2_and_site_macro_count():
    metrics = bench.metrics([1, 3], [2, 4])
    assert metrics == dict(n=2, mae=1.0, rmse=1.0, bias=1.0, r2=0.0)
    assert bench.metrics([1, 1], [1, 1])["r2"] is None
    with pytest.raises(ValueError):
        bench.metrics([1], [float("nan")])
    rows = panel()[:2]
    summary = bench.summarize(rows, [8, 8])
    assert summary["site_year_crop_macro"]["n_groups"] == 1


@pytest.mark.parametrize("target,prediction", [(0.1, 0.2), (3.5941, 5.0), (0.1, 0.1)])
def test_identical_decimal_targets_have_undefined_r2(target, prediction):
    result = bench.metrics([target] * 3, [prediction] * 3)
    assert result["r2"] is None
    assert result["mae"] == pytest.approx(abs(prediction - target))
    assert result["rmse"] == pytest.approx(abs(prediction - target))
    assert result["bias"] == pytest.approx(prediction - target)


@pytest.mark.parametrize("candidate", bench.CANDIDATES)
def test_models_fit_only_explicit_training_membership(candidate):
    rows = panel()
    training = bench.split_panel(rows, "temporal", "early", "maize")["fit"]
    predict = bench.fit_predictor(candidate, training)
    test = bench.split_panel(rows, "temporal", "early", "maize")["test"]
    before = predict(test)
    changed = deepcopy(test)
    for row in changed:
        row["yield_t_ha"] = 1e8  # Target is not a predictor, including for trend/baselines.
    assert predict(changed) == before
    assert all(abs(p) < 100 for p in before)


def test_test_labels_cannot_select_model(monkeypatch):
    # Fast deterministic candidates preserve the real selection/split/evaluation path.
    calls = []

    def fit(name, training):
        calls.append({r["year"] for r in training})
        offset = bench.CANDIDATES.index(name) / 10
        return lambda rows: [float(r["year"] - 2010) + offset for r in rows]

    monkeypatch.setattr(bench, "fit_predictor", fit)
    original = panel()
    changed = deepcopy(original)
    for row in changed:
        if row["year"] == 2015:
            row["yield_t_ha"] += 10000
    first, second = bench.evaluate(original), bench.evaluate(changed)
    for regime in first:
        for lead in first[regime]:
            for crop in first[regime][lead]:
                a, b = first[regime][lead][crop], second[regime][lead][crop]
                assert a["selected"] == b["selected"]
                assert a["best_by_validation"] == b["best_by_validation"]
                for model in a["test"]:
                    assert a["test"][model]["predictions"] == b["test"][model]["predictions"]
    assert all(years == {2011, 2012} for years in calls)


def test_frozen_protocol_mismatch_blocks_before_data_access(tmp_path):
    protocol = tmp_path / "protocol.json"
    protocol.write_text("{}")
    with pytest.raises(ValueError, match="Frozen protocol"):
        bench.run(tmp_path, tmp_path, tmp_path / "out.json", protocol)
    assert not (tmp_path / "out.json").exists()


def test_label_availability_filters_by_target_issue_without_dropping_test_rows():
    rows = panel()
    for row in rows:
        if row["year"] == 2013 and row["crop"] == "wheat":
            row["label_available_on"] = "2014-07-15"
    early = bench.split_panel(rows, "temporal", "early", "wheat")
    later = bench.split_panel(rows, "temporal", "standard", "wheat")
    assert not early["select"]
    assert len(later["select"]) == 3
    assert len(early["test"]) == len(later["test"]) == 3
    assert all(r["label_available_on"] <= "2014-07-01" for r in early["fit"])


def test_insufficient_selection_reports_baselines_without_tuning(monkeypatch):
    rows = panel()
    for row in rows:
        if row["year"] == 2013:
            row["label_available_on"] = "2020-01-01"
    monkeypatch.setattr(bench, "fit_predictor", lambda name, fit: lambda rs: [3.0] * len(rs))
    report = bench.evaluate(rows)["temporal"]["early"]["wheat"]
    assert report["status"] == "insufficient_selection_baselines_only"
    assert report["best_by_validation"] is None
    assert set(report["test"]) == {"training_mean", "crop_mean", "crop_trend"}
