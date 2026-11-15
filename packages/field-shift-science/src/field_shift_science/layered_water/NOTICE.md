# Team research provenance

Balance, soil and meteorology equations originate from Ruslan Koritich's Field Shift
research at `matveyola/nasa_preparation`, branch `research/fieldshift-tasks-1-4`,
commit `468a03d79e596e9daab4cfdb20e9e0c8e2b83a92` (Git author Ruslan,
ruslankoritich@gmail.com). Original file hashes are in source-receipt.json.

This is team code reused at the repository owner's request. No upstream licence
file was provided; this notice does not declare an open-source licence or grant
third-party commercial redistribution rights. Downstream rights remain unverified.

Changes: package-relative imports; strict caller-supplied calendars, hydraulics,
initial water, runoff coefficients and snow/freeze assumptions; no Iowa calendars,
synthetic weather generator or global mutable calibration. Water-only experimental
baseline, never a validated yield or global recommendation model. Original
FROZEN.json has five mismatches at the received commit and cannot certify this
adaptation. Independent physics/invariant tests are separate from empirical accuracy.
