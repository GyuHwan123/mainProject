import cv2
import numpy as np


def preprocess_image(
    file_path,
    deskew: bool = True,
    upscale: bool = True,
    grayscale: bool = False,
) -> np.ndarray:
    """Prepare a document image for OCR without destroying color information."""
    image = cv2.imread(str(file_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to read image: {file_path}")

    if deskew:
        image = _deskew(image)

    if upscale:
        image = _upscale_small_document(image)

    image = _reduce_noise(image)
    image = _enhance_local_contrast(image)
    image = _sharpen_text_edges(image)

    if grayscale:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def _upscale_small_document(image: np.ndarray) -> np.ndarray:
    """Upscale only low-resolution inputs, capped to avoid excess memory use."""
    height, width = image.shape[:2]
    longest_side = max(height, width)
    if longest_side >= 1800:
        return image

    scale = min(2.0, 1800 / max(longest_side, 1))
    return cv2.resize(
        image,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )


def _reduce_noise(image: np.ndarray) -> np.ndarray:
    """Remove small compression noise while retaining character boundaries."""
    return cv2.bilateralFilter(image, d=5, sigmaColor=24, sigmaSpace=24)


def _enhance_local_contrast(image: np.ndarray) -> np.ndarray:
    """Apply CLAHE to luminance so uneven lighting does not hide faint text."""
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness, channel_a, channel_b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(lightness)
    return cv2.cvtColor(
        cv2.merge((enhanced, channel_a, channel_b)),
        cv2.COLOR_LAB2BGR,
    )


def _sharpen_text_edges(image: np.ndarray) -> np.ndarray:
    """Use a restrained unsharp mask to clarify strokes without heavy halos."""
    blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=1.0)
    return cv2.addWeighted(image, 1.35, blurred, -0.35, 0)


def _deskew(image: np.ndarray) -> np.ndarray:
    """Correct modest document skew while ignoring implausible rotations."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    threshold = cv2.threshold(
        blurred,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )[1]

    coords = np.column_stack(np.where(threshold > 0))
    if len(coords) < 50:
        return image

    angle = cv2.minAreaRect(coords)[-1]
    if angle > 45:
        angle -= 90
    elif angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if not 0.5 < abs(angle) < 15:
        return image

    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
