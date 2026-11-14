"""Private Delineate Anything v2 inference for one RGB image chip."""

import argparse
import hashlib
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from urllib.request import urlopen

CHIP_SIZE = 256
CHIP_CENTER = CHIP_SIZE // 2
RGB_BYTES = CHIP_SIZE * CHIP_SIZE * 3
MAX_MASKS = 6
WEIGHTS_SHA256 = "46700b8a279b07922953a11adaeb5e658d9a2384b6334c8e0a3090886218915a"
WEIGHTS_SIZE = 124_747_297
WEIGHTS_URL = (
    "https://huggingface.co/MykolaL/DelineateAnything/resolve/"
    "369d0b4c44cf9bec2bd3a27bc81810cadd2c963e/DelineateAnythingv2.pt"
)
logger = logging.getLogger(__name__)


def verify_weights(weights: Path) -> None:
    with weights.open("rb") as model_file:
        digest = hashlib.file_digest(model_file, "sha256").hexdigest()
    if digest != WEIGHTS_SHA256:
        raise ValueError("Weights do not match the official DelineateAnythingv2.pt checksum.")


def download_weights(weights: Path) -> None:
    print("Downloading the official Delineate Anything v2 weights", flush=True)
    with NamedTemporaryFile(dir=weights.parent, delete=False) as checkpoint:
        temporary_path = Path(checkpoint.name)
    try:
        with urlopen(WEIGHTS_URL, timeout=30) as response, temporary_path.open("wb") as checkpoint:
            remaining = WEIGHTS_SIZE
            while chunk := response.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError("The checkpoint exceeds its expected size.")
                checkpoint.write(chunk)
        verify_weights(temporary_path)
        temporary_path.replace(weights)
    finally:
        temporary_path.unlink(missing_ok=True)


def load_predictor(weights: Path, device: str):
    # A .pt file can contain executable pickle. Only load the published, pinned artifact.
    verify_weights(weights)
    import cv2
    import numpy as np
    from ultralytics import YOLO

    model = YOLO(str(weights.resolve()), task="segment")

    def predict(image_bytes: bytes) -> bytes:
        rgb = np.frombuffer(image_bytes, dtype=np.uint8).reshape(CHIP_SIZE, CHIP_SIZE, 3)
        # Sentinel-2 uses 10 m pixels and the upstream model upsamples its native 256 px
        # chips for the 512 px input. Ultralytics expects BGR for NumPy inputs.
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        image = cv2.resize(bgr, (512, 512), interpolation=cv2.INTER_CUBIC)
        prediction = model.predict(
            image,
            imgsz=512,
            conf=0.15,
            retina_masks=True,
            max_det=128,
            device=device,
            verbose=False,
        )[0]
        if prediction.masks is None:
            return b""
        scores = prediction.boxes.conf.detach().cpu().numpy()
        ranked = np.argsort(-scores, kind="stable")
        masks = prediction.masks.data
        output = []
        for index in ranked:
            # Keep only masks covering the seed. Resize back to the native geospatial grid.
            grid = masks[int(index), 1::2, 1::2].detach().cpu().numpy() != 0
            if grid[CHIP_CENTER, CHIP_CENTER]:
                output.append(np.packbits(grid).tobytes())
            if len(output) == MAX_MASKS:
                break
        return b"".join(output)

    return predict


def run(host: str, port: int, weights: Path, device: str) -> None:
    predict = load_predictor(weights, device)
    slot = Lock()

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def do_POST(self) -> None:
            if self.path != "/predict":
                self.send_error(404)
                return
            if self.headers.get_content_type() != "application/octet-stream":
                self.send_error(415)
                return
            if self.headers.get("Content-Length") != str(RGB_BYTES):
                self.send_error(400, "Expected a 256 x 256 RGB image")
                return
            if not slot.acquire(timeout=3):
                self.send_error(429, "Boundary inference is busy")
                return
            try:
                image = self.rfile.read(RGB_BYTES)
                if len(image) != RGB_BYTES:
                    self.send_error(400, "Incomplete image")
                    return
                output = predict(image)
            except Exception:
                logger.exception("Boundary inference failed")
                self.send_error(503, "Boundary inference failed")
                return
            finally:
                slot.release()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            try:
                self.wfile.write(output)
            except OSError:
                # The API may already have fallen back after its network timeout.
                pass

    with ThreadingHTTPServer((host, port), Handler) as server:
        print(f"Boundary model listening on {host}:{port}", flush=True)
        server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--device", default="cpu", help="Use cpu or a CUDA device such as 0")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8091, type=int)
    parser.add_argument("--download-weights", action="store_true")
    args = parser.parse_args()
    if args.download_weights and not args.weights.exists():
        download_weights(args.weights)
    run(args.host, args.port, args.weights, args.device)


if __name__ == "__main__":
    main()
