"""Check the running model against the bundled Bondurant aerial image."""

import argparse
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import cv2
import numpy as np
from serve import CHIP_CENTER, CHIP_SIZE, MAX_MASKS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    args = parser.parse_args()
    url = args.url.rstrip("/")

    with urlopen(f"{url}/health/ready", timeout=3) as response:
        assert response.status == 200

    headers = {"Content-Type": "application/octet-stream"}
    try:
        with urlopen(Request(f"{url}/predict", b"short", headers), timeout=3) as response:
            raise AssertionError(f"Malformed chip unexpectedly returned {response.status}")
    except HTTPError as error:
        assert error.code == 400, f"Expected HTTP 400 for short chip, got {error.code}"

    aerial = cv2.imread(str(args.image))
    assert aerial is not None, f"Could not read {args.image}"
    # Use a known point inside the west field of the bundled 1300x1050 image.
    x, y, half_span = 188, 600, 175
    crop = aerial[y - half_span : y + half_span, x - half_span : x + half_span]
    chip = cv2.resize(crop, (CHIP_SIZE, CHIP_SIZE), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(chip, cv2.COLOR_BGR2RGB).tobytes()
    mask_bytes = CHIP_SIZE * CHIP_SIZE // 8
    with urlopen(Request(f"{url}/predict", rgb, headers), timeout=10) as response:
        assert response.headers.get_content_type() == "application/octet-stream"
        packed = response.read(MAX_MASKS * mask_bytes + 1)
    assert 0 < len(packed) <= MAX_MASKS * mask_bytes and len(packed) % mask_bytes == 0
    masks = np.unpackbits(np.frombuffer(packed, dtype=np.uint8)).reshape(-1, CHIP_SIZE, CHIP_SIZE)
    assert masks[:, CHIP_CENTER, CHIP_CENTER].all()
    assert not masks.all(axis=(1, 2)).any(), "A field mask filled the entire chip"
    print(f"Boundary model returned {len(masks)} masks covering the field point")


if __name__ == "__main__":
    main()
