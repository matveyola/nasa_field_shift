# Field boundary model

In field setup and the Fields map, a tap requests a proposed boundary. The API
reads a 256 × 256 RGB window centered on the tap and sends
only that image to the private inference process. It ranks candidate masks
covering the tap, discards outlines
that touch the image edge or cannot be stored in the field editor, and returns
the first valid polygon. The farmer reviews or edits the proposed boundary
before saving.

A missing or unusable result offers retry and manual drawing. The response
distinguishes unavailable imagery, imagery with no usable contour, a busy
model and an unavailable model. Deployments running the API directly without
a model URL still support browser segmentation.

## Run with Docker Desktop

From the repository root, run `docker compose up --build -d`. The boundary
service downloads the pinned [official weights][weights] once, verifies the
checksum and keeps them in a Docker volume. Its private `/health/ready` endpoint
responds once the model has loaded and completed a warmup inference. Field
entry remains available while the model starts, including manual drawing.
To reuse a local copy, run:

```powershell
$env:WEB_PORT = '8081'
$env:FIELD_SHIFT_BOUNDARY_MODEL_WEIGHTS = Join-Path $env:USERPROFILE 'Downloads\DelineateAnythingv2.pt'
docker compose -f compose.yaml -f compose.boundary-model.yaml -p field-shift-onboarding up --build -d
```

This starts a CPU model service with a read-only checkpoint mount.

CI builds and starts the CPU service when its inputs change and checks packed
masks through the private HTTP endpoint using the bundled Bondurant image. The
test needs the pinned weight download on first startup and reads local imagery.

The map overlays place names from [EOxMaps][eox] on its global satellite background
at navigation zooms. It uses more detailed imagery where available and can load
a recent Sentinel-2 window around the field. Set `VITE_MAPTILER_KEY` to use
MapTiler imagery outside the configured national aerial providers. The key is
public in the browser bundle and should be scoped to the intended origins.
MapTiler is for display. Choose a provider plan for the deployment's use.
Country and subdivision extents come from [Natural Earth][bounds] and
[Admin 1][regions] and serve as navigation presets.

For inference, the API tries the bundled Bondurant image and supported national
orthophoto services, then a recent Sentinel-2 scene. The global EOxCloudless 2016
mosaic is the final fallback. The model retries at a wider scale when the first
crop has no usable contour. The app displays the imagery source and date when
available.
To check the demo, open
`http://localhost:8081/fields`, choose **Add field → United States → Iowa · Bondurant**,
then tap inside the East Field. Review the proposed outline labeled
**Delineate Anything v2** before accepting the field.

## Run the model locally

1. Download [DelineateAnythingv2.pt][weights]. Keep it outside tracked files.
   The service verifies the SHA-256 published with the weights before loading.
2. In a separate Python environment, install Ultralytics and its runtime
   dependencies. Then run the private model process from the repository root.

   ```powershell
   $modelEnv = Join-Path $env:LOCALAPPDATA 'FieldShift\boundary-model-env'
   py -3.12 -m venv $modelEnv
   $modelPython = Join-Path $modelEnv 'Scripts\python.exe'
   & $modelPython -m pip install "ultralytics>=8.3.217,<9"
   & $modelPython services/boundary-model/serve.py --weights C:\path\to\DelineateAnythingv2.pt
   ```

3. For a local API process, set
   `FIELD_SHIFT_BOUNDARY_MODEL_URL=http://127.0.0.1:8091/predict` in its
   environment. For Docker Desktop, set
   `FIELD_SHIFT_BOUNDARY_MODEL_URL=http://host.docker.internal:8091/predict`
   in the Compose `.env`. Restart the API after setting the URL. If the
   container cannot reach the loopback listener, use a private Docker network
   or bind the inference service to a host interface that Docker can access.
   Keep the inference URL on a private network.

The model defaults to CPU. For CUDA, install a compatible PyTorch build in the
model environment and pass `--device 0`. The model process serves one inference
at a time and the API waits up to 10 seconds for the inference response, in
addition to the image lookup and read. Late results are not written to disk.
The Sentinel-2 inputs come from the same catalog integration as the existing
preview. Quality depends on scene conditions and field size.

[Delineate Anything v2][model] and its weights are distributed under AGPL-3.0.
This integration is independent of the upstream model source and does not
bundle the model checkpoint.

[model]: https://github.com/Lavreniuk/Delineate-Anything
[weights]: https://huggingface.co/MykolaL/DelineateAnything/blob/369d0b4/DelineateAnythingv2.pt
[bounds]: https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-0-details/
[regions]: https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-1-states-provinces/
[eox]: https://cloudless.eox.at/documentation/usage
