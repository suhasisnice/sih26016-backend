"""Face embedding extraction and comparison.

Uses face_recognition (dlib's ResNet face-recognition model under it) for
both enrollment and login — the same function on both ends of the
comparison, so a change of library later is one file, not a hunt through
every caller for where an embedding got produced.

The image itself never survives past this module. Every caller passes in
raw bytes and gets back either an embedding (a list of floats, meaningless
without the matching algorithm) or a reason it could not compute one; the
decoded image array is local to extract_embedding and is not returned,
logged, or written anywhere.
"""

import io
import json

import face_recognition
import numpy as np
from PIL import Image, UnidentifiedImageError

ALGORITHM = "face_recognition_dlib_resnet_v1"

# face_recognition's own documented default is 0.6. This system uses a
# tighter 0.5: the cost of a false reject here is someone falling back to
# fingerprint or a password, which they can always do; the cost of a false
# accept is a stranger opening an officer's account over a case with real
# people's compensation in it. Erring toward more rejects is the cheaper
# mistake by a wide margin.
MATCH_THRESHOLD = 0.5

# More than this many pixels on the long edge is discarded before face
# detection runs — a phone photo can be 4000px+ and face_recognition's HOG
# detector on the full image is the slowest part of either endpoint by far
# for no accuracy gained past a normal webcam frame's resolution.
MAX_DIMENSION = 1024


class FaceCaptureError(Exception):
    """A frame was decodable but unusable — never a caller's bug, always
    shown back to them verbatim so they know what to change and try
    again."""


def _decode(image_bytes: bytes) -> np.ndarray:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except UnidentifiedImageError as exc:
        raise FaceCaptureError("That doesn't look like an image. Try the capture again.") from exc

    image = image.convert("RGB")
    if max(image.size) > MAX_DIMENSION:
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
    return np.array(image)


def extract_embedding(image_bytes: bytes) -> list[float]:
    """One face's 128-d embedding, or raises FaceCaptureError explaining
    why not — no face, more than one, or too small/blurry to encode."""
    pixels = _decode(image_bytes)

    locations = face_recognition.face_locations(pixels)
    if not locations:
        raise FaceCaptureError(
            "No face detected. Make sure your face is centred in the frame and well lit."
        )
    if len(locations) > 1:
        raise FaceCaptureError(
            "More than one face is visible. Only the account holder should be in frame."
        )

    encodings = face_recognition.face_encodings(pixels, known_face_locations=locations)
    if not encodings:
        # Detected a face region but couldn't encode it — happens on
        # extreme motion blur or a face mostly out of frame at the edge.
        raise FaceCaptureError("Couldn't get a clear enough read on that face. Try again.")

    return encodings[0].tolist()


def serialise(embedding: list[float]) -> str:
    return json.dumps(embedding)


def deserialise(template: str) -> list[float]:
    return json.loads(template)


def distance(enrolled: list[float], attempt: list[float]) -> float:
    """Lower is more similar. 0.0 is an identical embedding; face_recognition
    documents differences past roughly 0.6 as reliably different people."""
    return float(face_recognition.face_distance([np.array(enrolled)], np.array(attempt))[0])


def matches(enrolled: list[float], attempt: list[float]) -> tuple[bool, float]:
    d = distance(enrolled, attempt)
    return d <= MATCH_THRESHOLD, d
