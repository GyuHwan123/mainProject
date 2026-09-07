import cv2
import numpy as np


def scale_bbox_to_image(
    bbox: list[list[int]],
    source_shape: tuple[int, ...],
    target_shape: tuple[int, ...],
) -> list[list[int]]:
    """Map OCR coordinates from a processed image back to the source image."""
    source_height, source_width = source_shape[:2]
    target_height, target_width = target_shape[:2]
    if source_width <= 0 or source_height <= 0:
        return bbox

    scale_x = target_width / source_width
    scale_y = target_height / source_height
    return [
        [
            max(0, min(round(point[0] * scale_x), target_width)),
            max(0, min(round(point[1] * scale_y), target_height)),
        ]
        for point in bbox
    ]


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
    """Upscale small OCR inputs without excessive memory use."""
    height, width = image.shape[:2]

    target_width = 1400

    if width >= target_width:
        return image

    scale = min(2.5, target_width / max(width, 1))

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
    """Correct modest receipt skew using dominant near-horizontal lines."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    edges = cv2.Canny(gray, 50, 150)

    height, width = image.shape[:2]

    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=max(50, width // 12),
        minLineLength=max(80, width // 4),
        maxLineGap=max(10, width // 50),
    )

    if lines is None:
        return image

    angles = []

    for x1, y1, x2, y2 in lines[:, 0]:
        dx = x2 - x1
        dy = y2 - y1

        if dx == 0:
            continue

        angle = np.degrees(np.arctan2(dy, dx))

        # 수평에 가까운 영수증 행/구분선만 사용
        if -15 <= angle <= 15:
            angles.append(angle)

    if len(angles) < 3:
        return image

    angle = float(np.median(angles))

    # 거의 수평이면 건드리지 않음
    if abs(angle) < 0.5:
        return image

    # 과도한 회전 방지
    if abs(angle) > 12:
        return image

    matrix = cv2.getRotationMatrix2D(
        (width / 2, height / 2),
        angle,
        1.0,
    )

    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
