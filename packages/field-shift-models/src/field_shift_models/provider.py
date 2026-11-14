"""Read-only CropModelProvider for the frozen planning-v1 port."""

from datetime import UTC, datetime

import numpy as np
from field_shift_contracts.planning_v1.common import EvidenceRef, Metric, Reason, Uncertainty
from field_shift_contracts.planning_v1.models import (
    HarvestBasis,
    ModelCard,
    ModelRef,
    SeasonEvaluation,
    SequenceEvaluation,
)

from .engine import NATIVE_CROP_NAMES, Parameters, RejectedInput, run_engine
from .provenance import digest, distribution_manifest

VERSION = "ospy-adapter-0.1.0"
SUPPORTED = tuple(NATIVE_CROP_NAMES)


def reason(code, message):
    return Reason(
        code=code,
        message=message,
        evidence=[
            EvidenceRef(
                id="ospy",
                kind="model",
                version=VERSION,
                locator="packages/field-shift-models/src/field_shift_models/provider.py",
            )
        ],
    )


def metric(unit, value=None, status="available", why=None):
    return Metric(
        status=status,
        value=value,
        lower=None,
        upper=None,
        unit=unit,
        uncertainty=Uncertainty(
            kind="none", coverage=None, calibration_scope=None, method_version=VERSION
        ),
        reasons=[why] if why else [],
    )


class AquaCropProvider:
    """Explicit hydraulics per immutable snapshot; no production fixture fallback.

    Generic crop names mean upstream built-in coefficients, never validated local hybrids.
    Optional footprint is (west, south, east, north); absent means generic baseline,
    not global scientific validation. No artifacts are promoted by this provider.
    """

    def __init__(self, hydraulics: dict[str, Parameters], footprint=None):
        import hashlib
        from pathlib import Path

        from aquacrop import Crop

        self.hydraulics = dict(hydraulics)
        self.footprint = footprint
        manifest = distribution_manifest("aquacrop")
        if manifest["version"] != "3.1.0":
            raise RuntimeError("Revalidate adapter against the installed AquaCrop release")
        source = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(__file__).parent.glob("*.py")
        }
        self._model = ModelRef(
            model_id="aquacrop-ospy",
            version="3.1.0",
            artifact_sha256=digest({"engine": manifest["files_sha256"], "adapter": source}),
            parameter_sha256=digest(
                {
                    "adapter": VERSION,
                    "crops": SUPPORTED,
                    "generic_crop_parameters": {
                        c: vars(Crop(NATIVE_CROP_NAMES[c], planting_date="04/01"))
                        for c in SUPPORTED
                    },
                    "hydraulics": {k: vars(v) for k, v in sorted(hydraulics.items())},
                    "footprint": footprint,
                }
            ),
        )
        self._licence = next(iter(manifest["licences"].values()))

    def model_card(self):
        from field_shift_contracts.planning_v1.common import CropRef

        return ModelCard(
            model=self._model,
            licence_identifier="Apache-2.0",
            licence_source="aquacrop-3.1.0.dist-info/licenses/LICENSE",
            licence_sha256=self._licence,
            commercial_use="permitted",
            data_permissions=[
                "Bundled crop coefficients: aquacrop 3.1.0 Apache-2.0; "
                "field inputs require separate permission"
            ],
            capabilities=[
                "yield",
                "phenology",
                "root_zone_water",
                "surface_moisture",
                "state_carry",
            ],
            supported_crops=[
                CropRef(crop_id=c, cultivar_id="ospy-generic", profile_version="3.1.0")
                for c in SUPPORTED
            ],
            supported_region_ids=[],
            validated_scope=None,
            training_status="insufficient_training_data",
            cohorts=[],
            promotion="baseline_only",
            gate_protocol_version="crop-gate-v1",
            gate_frozen_at=datetime(2026, 11, 14, tzinfo=UTC),
            holdout_opened_at=None,
            fallback_model=None,
            reasons=[
                reason(
                    "generic_baseline",
                    "Uncalibrated generic physical baseline; "
                    "no independent field/year evidence or predictive intervals",
                )
            ],
        )

    def evaluate(self, profile, candidate, weather):
        if (
            candidate.field_snapshot_id != profile.snapshot_id
            or candidate.field_id != profile.field_id
        ):
            raise ValueError("Candidate/profile scope mismatch")
        if profile.as_of > weather.as_of:
            raise ValueError("Profile as_of was unavailable at the weather issue time")
        traces = []
        for scenario in weather.scenarios:
            found = [t for t in scenario.traces if t.field_snapshot_id == profile.snapshot_id]
            if len(found) != 1:
                raise ValueError("Every trajectory requires exactly one matching field trace")
            traces.append(found[0])
        input_hash = digest(
            {
                "profile": profile.model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
                "weather": weather.model_dump(mode="json"),
                "model": self._model.model_dump(mode="json"),
            }
        )
        seasons, reasons = [], []
        for scenario, trace in zip(weather.scenarios, traces):
            try:
                self._check(profile, candidate, weather)
                result = run_engine(
                    profile, candidate.assignments, trace.days, self.hydraulics[profile.snapshot_id]
                )
                seasons.extend(self._seasons(candidate, scenario.trajectory_id, result, input_hash))
            except RejectedInput as exc:
                why = reason(exc.code, str(exc))
                reasons.append(why)
                for assignment in candidate.assignments:
                    seasons.append(
                        SeasonEvaluation(
                            season=assignment.season,
                            crop=assignment.crop,
                            trajectory_id=scenario.trajectory_id,
                            status=exc.status,
                            yield_t_ha=metric("t/ha", status=exc.status, why=why),
                            harvest_basis=None,
                            root_zone_reserve_mm=metric("mm", status=exc.status, why=why),
                            root_depth_cm=None,
                            surface_moisture_m3_m3=metric("m3/m3", status=exc.status, why=why),
                            surface_depth_top_cm=None,
                            surface_depth_bottom_cm=None,
                            stress_days=metric("days", status=exc.status, why=why),
                            irrigation_mm=metric("mm", status=exc.status, why=why),
                            opening_state_id=None,
                            closing_state_id=None,
                            reasons=[why],
                        )
                    )
        statuses = {s.status for s in seasons}
        applicability = (
            "out_of_domain"
            if "out_of_domain" in statuses
            else "unsupported"
            if "unsupported" in statuses
            else "baseline"
        )
        return SequenceEvaluation(
            evaluation_id="eval-" + input_hash[:32],
            sequence_id=candidate.sequence_id,
            field_snapshot_id=profile.snapshot_id,
            scenario_set_id=weather.scenario_set_id,
            as_of=weather.as_of,
            model=self._model,
            applicability=applicability,
            uncertainty_scope="Deterministic generic simulations per supplied "
            "trajectory; no calibrated interval or probability. Synthetic inputs "
            "are invariant tests, not field accuracy evidence.",
            seasons=seasons,
            input_sha256=input_hash,
            reasons=reasons[:99]
            + [reason("baseline", "No local validation; no automatic promotion")],
        )

    def _check(self, profile, candidate, weather):
        if profile.management.residue_cover_fraction not in (None, 0.0) or (
            profile.management.tillage not in ("unknown", "conventional")
        ):
            raise RejectedInput(
                "unsupported",
                "management",
                "Residue/tillage response is not supported by this adapter",
            )
        if len(weather.scenarios) > 8 or len(weather.scenarios[0].traces[0].days) > 1100:
            raise RejectedInput(
                "unsupported", "resource_limit", "At most 8 trajectories and 1100 days"
            )
        if any(s.quality != "valid" for s in profile.sources + weather.sources):
            raise RejectedInput("insufficient_data", "source_quality", "Valid sources required")
        if profile.land_status != "arable":
            raise RejectedInput("unsupported", "land", "An evidenced arable field is required")
        if self.footprint:
            if not profile.terrain:
                raise RejectedInput(
                    "insufficient_data", "location", "Footprint check requires coordinates"
                )
            w, s, e, n = self.footprint
            if not (
                w <= profile.terrain.longitude_deg <= e and s <= profile.terrain.latitude_deg <= n
            ):
                raise RejectedInput(
                    "out_of_domain", "footprint", "Outside configured baseline footprint"
                )
        for a, assessment in zip(candidate.assignments, candidate.assessments):
            if (
                a.crop.crop_id not in SUPPORTED
                or a.crop.cultivar_id != "ospy-generic"
                or (a.crop.profile_version != "3.1.0")
            ):
                raise RejectedInput(
                    "unsupported", "cultivar", "Only pinned upstream generic profiles supported"
                )
            if assessment.status != "eligible":
                raise RejectedInput(
                    "unsupported", "suitability", "Candidate is not explicitly eligible"
                )
            missing = set(assessment.required_capabilities) - set(self.model_card().capabilities)
            if missing:
                raise RejectedInput(
                    "unsupported",
                    "capability",
                    "Unsupported capabilities: " + ",".join(sorted(missing)),
                )
        if profile.hazards:
            raise RejectedInput(
                "unsupported", "hazard", "Hazard impact needs a separately validated model"
            )
        if profile.snapshot_id not in self.hydraulics:
            raise RejectedInput(
                "insufficient_data", "hydraulics", "No pinned hydraulic parameters for snapshot"
            )
        if self.hydraulics[profile.snapshot_id].synthetic and weather.scenario_type != "synthetic":
            raise RejectedInput(
                "unsupported",
                "synthetic_parameters",
                "Synthetic parameters require synthetic weather",
            )

    def _seasons(self, candidate, trajectory, result, input_hash):
        rows = []
        previous = "state-" + digest([input_hash, trajectory, "opening"])[:32]
        for i, assignment in enumerate(candidate.assignments):
            harvest = result.harvests.iloc[i]
            harvest_date = harvest["Harvest Date (YYYY/MM/DD)"].date()
            if not assignment.sowing_window.start <= harvest_date <= assignment.harvest_window.end:
                raise RuntimeError("Engine harvest outside declared calendar")
            # Closing state includes the full interseason gap through the eve of next sowing.
            if i + 1 < len(candidate.assignments):
                next_sow = candidate.assignments[i + 1].sowing_window.start
                index = next(j for j, d in enumerate(result.dates) if d == next_sow) - 1
            else:
                index = len(result.dates) - 1
            closing = (
                "state-"
                + digest(
                    [
                        input_hash,
                        trajectory,
                        result.dates[index].isoformat(),
                        result.theta[index].tolist(),
                        float(result.flux.iloc[index].surface_storage),
                    ]
                )[:32]
            )
            # Reserve integrates plant-available VWC over the actual harvest root depth.
            # OSPy reports the next-day timestamp; Step indexes the simulated harvest day.
            hi = int(harvest["Harvest Date (Step)"])
            root = float(result.growth.iloc[hi].z_root)
            tops = np.r_[0, np.cumsum(result.dz_m)[:-1]]
            overlap = np.clip(root - tops, 0, result.dz_m)
            reserve = float(
                (
                    np.clip(result.theta[hi] - result.wp, 0, result.fc - result.wp) * overlap * 1000
                ).sum()
            )
            flux = result.flux[(result.flux.season_counter == i) & (result.flux.dap > 0)]
            stress = int(((flux.TrPot > 1e-8) & (flux.Tr < flux.TrPot - 1e-6)).sum())
            rows.append(
                SeasonEvaluation(
                    season=assignment.season,
                    crop=assignment.crop,
                    trajectory_id=trajectory,
                    status="available",
                    yield_t_ha=metric("t/ha", float(harvest["Dry yield (tonne/ha)"])),
                    harvest_basis=HarvestBasis(basis="dry_matter", moisture_fraction=0.0),
                    root_zone_reserve_mm=metric("mm", reserve),
                    root_depth_cm=root * 100,
                    surface_moisture_m3_m3=metric("m3/m3", float(result.theta[hi, 0])),
                    surface_depth_top_cm=0.0,
                    surface_depth_bottom_cm=float(result.dz_m[0] * 100),
                    stress_days=metric("days", float(stress)),
                    irrigation_mm=metric("mm", float(harvest["Seasonal irrigation (mm)"])),
                    opening_state_id=previous,
                    closing_state_id=closing,
                    reasons=[
                        reason(
                            "uncalibrated",
                            "Generic coefficients; no fertility/salinity/hazard "
                            "response. Stress = transpiration-limited active crop days. "
                            "Water metrics are at harvest; state ID includes interseason gap.",
                        ),
                        reason(
                            "calendar",
                            f"Sown {assignment.sowing_window.start.isoformat()}; "
                            f"engine harvest timestamp {harvest_date.isoformat()}; "
                            f"last water-state date {result.dates[index].isoformat()} UTC. "
                            "OSPy harvest timestamp is the next-day boundary; "
                            "daily root/surface metrics use its harvest step.",
                        ),
                    ],
                )
            )
            previous = closing
        return rows
