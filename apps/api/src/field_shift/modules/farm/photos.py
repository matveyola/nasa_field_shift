from base64 import b64decode
from binascii import Error as Base64Error

PHOTO_MAX_BYTES = 512 * 1024
PHOTO_MAX_EDGE = 1600
PHOTO_ERROR = "Attach a valid JPEG photo up to 512 KiB and 1600 pixels per edge."


def decode_observation_photo(value: str) -> bytes:
    # Bound the encoded input before allocating its decoded form.
    if len(value) > 4 * ((PHOTO_MAX_BYTES + 2) // 3):
        raise ValueError(PHOTO_ERROR)
    try:
        image = b64decode(value, validate=True)
    except (Base64Error, ValueError) as error:
        raise ValueError(PHOTO_ERROR) from error
    if len(image) > PHOTO_MAX_BYTES or not _has_bounded_jpeg_frame(image):
        raise ValueError(PHOTO_ERROR)
    return image


def _has_bounded_jpeg_frame(image: bytes) -> bool:
    """Validate framing and dimensions without decoding untrusted pixels on the server."""
    if not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
        return False
    offset = 2
    found_frame = False
    while offset + 4 < len(image) and image[offset] == 0xFF:
        while offset < len(image) and image[offset] == 0xFF:
            offset += 1
        if offset + 3 > len(image):
            return False
        marker = image[offset]
        size = int.from_bytes(image[offset + 1 : offset + 3], "big")
        end = offset + 1 + size
        if size < 2 or end > len(image) - 2:
            return False
        if marker == 0xDA:  # Start of scan
            return found_frame and size >= 6 and end < len(image) - 2
        if marker in {0xC0, 0xC1, 0xC2}:
            if found_frame or size < 11:
                return False
            height = int.from_bytes(image[offset + 4 : offset + 6], "big")
            width = int.from_bytes(image[offset + 6 : offset + 8], "big")
            if not (1 <= height <= PHOTO_MAX_EDGE and 1 <= width <= PHOTO_MAX_EDGE):
                return False
            found_frame = True
        offset = end
    return False
