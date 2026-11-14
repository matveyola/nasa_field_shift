# Field-boundary evaluation

This runs the browser's TypeScript segmentation engine on captured image windows with manually drawn reference polygons. Reference polygons and neighboring fields should come from an independent trace rather than the proposals being scored.

From the repository root with Node 24 or newer:

```sh
node apps/web/evaluation/segmentation/evaluate.mjs manifest.json --output report.json
node apps/web/evaluation/segmentation/evaluate.mjs manifest.json --engine baseline=baseline.ts --engine candidate=apps/web/src/features/fields/segmentationEngine.ts --split holdout --output holdout.json
node --test apps/web/evaluation/segmentation/evaluate.test.mjs
```

The manifest has a `cases` array. A case identifies one reference field in an image window. Repeated captures of the same field may use distinct case IDs and a common field ID.

```json
{
  "cases": [
    {
      "id": "site-1/field-1/window-1",
      "site": "site-1",
      "split": "holdout",
      "source": "satellite",
      "field": "field-1",
      "scene": "Sentinel-2 scene identifier",
      "image": { "path": "windows/site-1-field-1.rgba", "width": 240, "height": 240 },
      "reference": [[40, 40], [100, 40], [100, 100], [40, 100]],
      "neighbors": [{ "id": "field-2", "reference": [[100, 40], [155, 40], [155, 100], [100, 100]] }],
      "seeds": [{ "id": "center", "point": [70, 70] }]
    }
  ]
}
```

The image path is relative to the manifest. Each window is a headerless row-major RGBA byte array from the application's imagery loader. The origin is the top left and points use pixel-edge coordinates. References and adjacent outlines are rasterized at pixel centers. Use `tuning`, `holdout` or `regression` for the split and keep an entire site in one split. `field`, `scene` and `neighbors` are optional. References extending outside a window are scored over the visible portion and flagged in the output.

The report records every seed, its outline and vertex count, area IoU and recall, boundary F1 within two raster pixels, and inference time. Mean IoU and boundary F1 give missing or unusable proposals a score of zero. Acceptance and accepted-only IoU are separate so an engine cannot improve its reported area overlap simply by rejecting difficult examples.

Leakage is the share of a proposal outside the target polygon. The report flags leakage of 20% or more. For each annotated neighbor it reports the share of the proposal in that neighbor and the share of that neighbor covered. Neighbor pixels also inside the target trace are excluded. A merge flag requires that the proposal extend more than two pixels into the neighbor and that those pixels cover at least 10% of both the proposal and the neighbor. The neighbor-annotated seed count is reported separately. Timing covers the engine call in Node and excludes imagery loading and scoring.

For multispectral comparisons, add an optional `spectralFrames` array to each case. Each entry has a `scene` identifier and a `path` relative to the manifest. The file contains two consecutive row-major little-endian float32 planes on the exact RGB grid, first NIR surface reflectance and then NDVI. Unusable pixels use NaN. Put the RGB anchor date first and older acquisitions after it. Pass `--frames single=1 --frames temporal=3` with corresponding `--engine` names to compare one date and up to three dates. Engines without a frame option receive only RGB. The report records the actual scene IDs supplied on each click.
