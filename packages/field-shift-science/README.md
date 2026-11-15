# Field Shift shared scientific calculations

`field-shift-science` owns the shared crop, geometry, weather, water-balance and
inference calculations used by API and offline workflows. Its import namespace
is `field_shift_science`; domain subdirectories preserve their ownership. It has
only Pydantic and Redis dependencies, with no API server, ORM, raster stack or
training framework.

API application modules still own HTTP, persistence, permissions, jobs and
orchestration. Scientific functions and immutable model artifacts have one
implementation shared by those modules and `field-shift-ml`; do not copy them or
import another project's `src` directory. Independent consumer locks preserve ML's
NumPy 1.26 and the API/models NumPy 2 environments.

Artifacts are installed package data in `geo_ml/artifacts`, `simulation/artifacts`
and `rotations/artifacts`. Load them through `importlib.resources`; never derive
paths from the process working directory. Their bytes, versions, negative release
gates and promotion rules are preserved. Training writes candidate artifacts to
explicit output paths; promoting a candidate remains a separately reviewed change.
