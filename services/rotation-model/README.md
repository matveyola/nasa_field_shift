# Private experimental rotation runtime

Build from repository root with `docker build -f services/rotation-model/Dockerfile`.
The image installs `field-shift-rotation-model` noneditably and runs as UID10001.
Expose it only on the private worker network. Set a dedicated
`FIELD_SHIFT_ROTATION_MODEL_TOKEN` (minimum32 characters), shared with API/workers.
PORT defaults to8080. The service has no database or Redis access.

POST /catalog, /acquire and /evaluate require Bearer authentication. Acquisition
and replay are separate phases; explicit provider consent does not override host,
response-size, time or process-memory limits. See the owning package README for
capabilities, operational bounds, durable recovery and scientific limitations.
