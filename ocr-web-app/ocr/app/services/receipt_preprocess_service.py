"""Receipt-oriented preprocessing with reversible geometry."""

from dataclasses import asdict, dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class PreprocessOptions:
    # PaddleOCR also has document-unwarping support. Applying projective
    # correction in both places makes the returned detection coordinates
    # unreliable, so keep the reversible affine steps as the safe default.
    perspective_correction: bool = True
    crop: bool = True
    deskew: bool = True
    upscale: bool = True
    illumination_correction: bool = True
    contrast_enhancement: bool = True
    closing: bool = True
    sharpen: bool = True

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass
class ReceiptPreprocessResult:
    image: np.ndarray
    original_shape: tuple[int, ...]
    forward_transform: np.ndarray
    applied_steps: list[str]

    def bbox_to_original(self, bbox: list[list[int | float]]) -> list[list[int]]:
        points = np.asarray(bbox, dtype=np.float64).reshape(-1, 2)
        if not len(points):
            return bbox
        x1, y1 = points.min(axis=0)
        x2, y2 = points.max(axis=0)
        corners = np.asarray([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float64)
        inverse = np.linalg.inv(self.forward_transform)
        homogeneous = np.column_stack([corners, np.ones(len(corners))])
        mapped = (inverse @ homogeneous.T).T
        mapped = mapped[:, :2] / np.maximum(np.abs(mapped[:, 2:3]), 1e-8)
        height, width = self.original_shape[:2]
        left, top = np.floor(mapped.min(axis=0)).astype(int)
        right, bottom = np.ceil(mapped.max(axis=0)).astype(int)
        return [[max(0, min(left, width)), max(0, min(top, height))],
                [max(0, min(right, width)), max(0, min(bottom, height))]]


def preprocess_receipt_image(file_path, options: PreprocessOptions | None = None) -> ReceiptPreprocessResult:
    options = options or PreprocessOptions()
    image = cv2.imread(str(file_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to read receipt image: {file_path}")
    original_shape = image.shape
    transform = np.eye(3, dtype=np.float64)
    steps: list[str] = []

    def apply_geometry(operation, name: str) -> None:
        nonlocal image, transform
        next_image, matrix = operation(image)
        if matrix is not None:
            image = next_image
            transform = matrix @ transform
            steps.append(name)

    if options.perspective_correction:
        apply_geometry(_correct_perspective, "perspective_correction")
    if options.deskew:
        apply_geometry(_deskew, "deskew")
    if options.crop:
        apply_geometry(_crop_to_receipt_content, "crop")
    if options.upscale:
        apply_geometry(_upscale_receipt, "upscale")
    if options.illumination_correction:
        next_image = _correct_uneven_illumination(image)
        if next_image is not image:
            image = next_image; steps.append("illumination_correction")
    if options.contrast_enhancement:
        image = _enhance_local_contrast(image); steps.append("contrast_enhancement")
    if options.closing:
        image = _close_text_strokes(image); steps.append("closing")
    if options.sharpen:
        image = _sharpen_text_edges(image); steps.append("sharpen")
    return ReceiptPreprocessResult(image, original_shape, transform, steps)


def _correct_perspective(image: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    height, width = image.shape[:2]
    if min(height, width) < 80:
        return image, None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[0]
    image_area = float(height * width)
    corners = None
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:20]:
        area = float(cv2.contourArea(contour))
        if not 0.12 * image_area <= area <= 0.98 * image_area:
            continue
        candidate = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(candidate) != 4 or not cv2.isContourConvex(candidate):
            continue
        ordered = _order_corners(candidate.reshape(4, 2).astype(np.float32))
        box_area = float(cv2.contourArea(cv2.boxPoints(cv2.minAreaRect(ordered))))
        if box_area <= 0 or area / box_area < 0.60 or not _plausible_corners(ordered, width, height):
            continue
        if not _has_perspective_distortion(ordered):
            continue
        corners = ordered
        break
    if corners is None:
        return image, None
    tl, tr, br, bl = corners
    target_width = round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    target_height = round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    if target_width < 80 or target_height < 80:
        return image, None
    px, py = max(1, round(target_width * .03)), max(1, round(target_height * .03))
    destination = np.asarray([[px, py], [px + target_width - 1, py],
                              [px + target_width - 1, py + target_height - 1],
                              [px, py + target_height - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(corners, destination).astype(np.float64)
    output = cv2.warpPerspective(image, matrix, (target_width + 2 * px, target_height + 2 * py),
                                 flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return output, matrix


def _order_corners(points: np.ndarray) -> np.ndarray:
    ordered = np.empty((4, 2), dtype=np.float32)
    sums, differences = points.sum(axis=1), np.diff(points, axis=1).ravel()
    ordered[0], ordered[2] = points[np.argmin(sums)], points[np.argmax(sums)]
    ordered[1], ordered[3] = points[np.argmin(differences)], points[np.argmax(differences)]
    return ordered


def _plausible_corners(points: np.ndarray, width: int, height: int) -> bool:
    lengths = [float(np.linalg.norm(points[(i + 1) % 4] - points[i])) for i in range(4)]
    if min(lengths) < min(width, height) * .08:
        return False
    angles = []
    for i in range(4):
        previous, following = points[i - 1] - points[i], points[(i + 1) % 4] - points[i]
        cosine = np.dot(previous, following) / max(np.linalg.norm(previous) * np.linalg.norm(following), 1e-6)
        angles.append(float(np.degrees(np.arccos(np.clip(cosine, -1, 1)))))
    if any(angle < 35 or angle > 145 for angle in angles):
        return False
    margin = max(3, round(min(width, height) * .005))
    return sum(x <= margin or y <= margin or x >= width - 1 - margin or y >= height - 1 - margin
               for x, y in points) < 4


def _has_perspective_distortion(points: np.ndarray) -> bool:
    tl, tr, br, bl = points
    top, right = float(np.linalg.norm(tr - tl)), float(np.linalg.norm(br - tr))
    bottom, left = float(np.linalg.norm(br - bl)), float(np.linalg.norm(bl - tl))
    return max(top, bottom) / max(min(top, bottom), 1e-6) >= 1.06 or max(left, right) / max(min(left, right), 1e-6) >= 1.06


def _deskew(image: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 150)
    minimum_length = max(30, min(image.shape[:2]) // 5)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 50, minLineLength=minimum_length, maxLineGap=15)
    angles = []
    if lines is not None:
        for x1, y1, x2, y2 in lines.reshape(-1, 4):
            angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            if -7 <= angle <= 7:
                angles.append(float(angle))
    if len(angles) < 4:
        return image, None
    median = float(np.median(angles))
    inliers = np.asarray(angles)[np.abs(np.asarray(angles) - median) <= 1.5]
    if len(inliers) < 4 or len(inliers) / len(angles) < .65:
        return image, None
    angle = float(np.median(inliers))
    if abs(angle) < .75:
        return image, None
    height, width = image.shape[:2]
    affine = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    matrix = np.vstack([affine, [0, 0, 1]]).astype(np.float64)
    return cv2.warpAffine(image, affine, (width, height), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE), matrix


def _crop_to_receipt_content(image: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    mask = cv2.threshold(gray, 215, 255, cv2.THRESH_BINARY_INV)[1]
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    points = cv2.findNonZero(mask)
    if points is None:
        return image, None
    x, y, content_width, content_height = cv2.boundingRect(points)
    margin = max(20, round(min(height, width) * .03))
    x1, y1 = max(0, x - margin), max(0, y - margin)
    x2, y2 = min(width, x + content_width + margin), min(height, y + content_height + margin)
    if (x2 - x1) < width * .25 or (y2 - y1) < height * .25 or (x1 == 0 and y1 == 0 and x2 == width and y2 == height):
        return image, None
    matrix = np.asarray([[1, 0, -x1], [0, 1, -y1], [0, 0, 1]], dtype=np.float64)
    return image[y1:y2, x1:x2], matrix


def _upscale_receipt(image: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    height, width = image.shape[:2]
    short_side, long_side = min(height, width), max(height, width)
    if short_side >= 1200:
        return image, None
    scale = min(2.5, 1200 / max(short_side, 1), 3200 / max(long_side, 1))
    if scale <= 1:
        return image, None
    matrix = np.asarray([[scale, 0, 0], [0, scale, 0], [0, 0, 1]], dtype=np.float64)
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC), matrix


def _correct_uneven_illumination(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness, channel_a, channel_b = cv2.split(lab)
    kernel = max(31, (min(lightness.shape) // 12) | 1)
    background = cv2.GaussianBlur(lightness, (kernel, kernel), 0)
    low, middle, high = np.percentile(background, (10, 50, 90))
    if (middle >= 190 and low >= 155) or high - low < 28 or float(np.std(background)) < 18:
        return image
    corrected = cv2.addWeighted(lightness, .6, cv2.divide(lightness, background, scale=255), .4, 0)
    return cv2.cvtColor(cv2.merge((corrected, channel_a, channel_b)), cv2.COLOR_LAB2BGR)


def _enhance_local_contrast(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness, channel_a, channel_b = cv2.split(lab)
    enhanced = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8)).apply(lightness)
    return cv2.cvtColor(cv2.merge((enhanced, channel_a, channel_b)), cv2.COLOR_LAB2BGR)


def _close_text_strokes(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness, channel_a, channel_b = cv2.split(lab)
    restored = cv2.bitwise_not(cv2.morphologyEx(cv2.bitwise_not(lightness), cv2.MORPH_CLOSE,
                                                np.ones((3, 3), dtype=np.uint8)))
    return cv2.cvtColor(cv2.merge((restored, channel_a, channel_b)), cv2.COLOR_LAB2BGR)


def _sharpen_text_edges(image: np.ndarray) -> np.ndarray:
    return cv2.addWeighted(image, 1.25, cv2.GaussianBlur(image, (0, 0), sigmaX=.8), -.25, 0)
