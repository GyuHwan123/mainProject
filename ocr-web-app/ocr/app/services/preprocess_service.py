import cv2
import numpy as np


def preprocess_image(
    file_path,
    deskew: bool = True,
    upscale: bool = False,
    grayscale: bool = False,
) -> np.ndarray:
    """
    OCR 전 이미지 전처리.

    현재 적용 가능한 전처리:
    - deskew: 이미지 기울기 보정
    - upscale: 이미지 2배 확대
    - grayscale: 흑백 변환

    기본값:
    - deskew=True
    - upscale=True
    - grayscale=False

    반환값:
        전처리된 이미지 (numpy.ndarray)
    """

    # ---------------------------------
    # 1. 이미지 읽기
    # ---------------------------------

    img = cv2.imread(str(file_path))

    if img is None:
        raise ValueError(
            f"이미지를 읽을 수 없습니다: {file_path}"
        )

    # ---------------------------------
    # 2. 기울기 보정
    # ---------------------------------

    if deskew:
        img = _deskew(img)

    # ---------------------------------
    # 3. 이미지 확대
    # ---------------------------------

    if upscale:
        img = cv2.resize(
            img,
            None,
            fx=2,
            fy=2,
            interpolation=cv2.INTER_CUBIC,
        )

    # ---------------------------------
    # 4. Grayscale
    # ---------------------------------

    if grayscale:
        img = cv2.cvtColor(
            img,
            cv2.COLOR_BGR2GRAY,
        )

    return img


def _deskew(img: np.ndarray) -> np.ndarray:
    """
    이미지의 전체적인 기울기를 추정하고 보정한다.

    현재는 기존 팀원의 deskew 로직을 기반으로 한다.

    0.5도 이하:
        거의 정상적인 이미지로 판단

    0.5 ~ 15도:
        기울어진 이미지로 판단하고 보정

    15도 초과:
        일반적인 문서 기울기로 보기 어려우므로
        회전하지 않는다.
    """

    # ---------------------------------
    # Grayscale
    # ---------------------------------

    gray = cv2.cvtColor(
        img,
        cv2.COLOR_BGR2GRAY,
    )

    # ---------------------------------
    # Blur
    #
    # OCR 입력용이 아니라
    # 기울기 계산을 안정화하기 위한 용도
    # ---------------------------------

    blur = cv2.GaussianBlur(
        gray,
        (9, 9),
        0,
    )

    # ---------------------------------
    # Otsu Threshold
    #
    # OCR 입력으로 사용하지 않는다.
    # 기울기 계산용이다.
    # ---------------------------------

    _, thresh = cv2.threshold(
        blur,
        0,
        255,
        cv2.THRESH_BINARY_INV
        + cv2.THRESH_OTSU,
    )

    # ---------------------------------
    # foreground pixel 좌표 추출
    # ---------------------------------

    coords = np.column_stack(
        np.where(thresh > 0)
    )

    # 이미지에 유효한 foreground가 없는 경우
    if len(coords) == 0:
        return img

    # ---------------------------------
    # 최소 영역 사각형으로 각도 계산
    # ---------------------------------

    angle = cv2.minAreaRect(coords)[-1]

    # ---------------------------------
    # OpenCV 각도 정규화
    # -45 ~ 45도 범위
    # ---------------------------------

    if angle > 45:
        angle = angle - 90

    elif angle < -45:
        angle = -(90 + angle)

    else:
        angle = -angle

    # ---------------------------------
    # 스마트 기울기 보정
    # ---------------------------------

    if 0.5 < abs(angle) < 15:

        height, width = img.shape[:2]

        center = (
            width // 2,
            height // 2,
        )

        matrix = cv2.getRotationMatrix2D(
            center,
            angle,
            1.0,
        )

        img = cv2.warpAffine(
            img,
            matrix,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )

    return img