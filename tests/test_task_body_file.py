"""Bounding-box format converters and dataset schema translators.

Supported bbox formats

- **xyxy**: ``[x_min, y_min, x_max, y_max]`` (absolute pixels)
- **xywh**: ``[x_min, y_min, width, height]``  (COCO-style)
- **cxcywh**: ``[center_x, center_y, width, height]``
- **normalized / YOLO**: ``[cx, cy, w, h]`` in ``[0, 1]`` relative to image size

Supported dataset schemas

- COCO JSON
- YOLO txt (one ``.txt`` per image)
- Pascal VOC XML
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from xml.dom import minidom

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Pydantic models for validation
# ---------------------------------------------------------------------------

class BBox(BaseModel):
    """A single bounding box with format metadata."""

    coords: list[float]
    fmt: str  # "xyxy" | "xywh" | "cxcywh" | "normalized"

    @field_validator("coords")
    @classmethod
    def _four_elements(cls, v: list[float]) -> list[float]:
        if len(v) != 4:
            msg = f"coords must have exactly 4 elements, got {len(v)}"
            raise ValueError(msg)
        return v

    @field_validator("fmt")
    @classmethod
    def _valid_fmt(cls, v: str) -> str:
        allowed = {"xyxy", "xywh", "cxcywh", "normalized"}
        if v not in allowed:
            msg = f"fmt must be one of {allowed}, got {v!r}"
            raise ValueError(msg)
        return v

class COCOAnnotation(BaseModel):
    """Minimal COCO annotation record."""

    id: int
    image_id: int
    category_id: int
    bbox: list[float]  # xywh
    area: float
    iscrowd: int = 0

    @field_validator("bbox")
    @classmethod
    def _four(cls, v: list[float]) -> list[float]:
        if len(v) != 4:
            msg = f"bbox must have 4 elements, got {len(v)}"
            raise ValueError(msg)
        return v

class COCOImage(BaseModel):
    """Minimal COCO image record."""

    id: int
    file_name: str
    width: int
    height: int

    @field_validator("width", "height")
    @classmethod
    def _positive_dimension(cls, v: int) -> int:
        if v <= 0:
            msg = f"image dimensions must be positive, got {v}"
            raise ValueError(msg)
        return v

class COCOCategory(BaseModel):
    """Minimal COCO category record."""

    id: int
    name: str
    supercategory: str = ""

class COCODataset(BaseModel):
    """Top-level COCO dataset container."""

    images: list[COCOImage]
    annotations: list[COCOAnnotation]
    categories: list[COCOCategory]

# ---------------------------------------------------------------------------
# Bbox format conversions (vectorized NumPy)
# ---------------------------------------------------------------------------

def xyxy_to_xywh(
    boxes: NDArray[np.floating[Any]],
    sanitize: bool = False,
) -> NDArray[np.floating[Any]]:
    """Convert ``(N, 4)`` boxes from xyxy to xywh format.

    Parameters

    boxes : ndarray, shape ``(N, 4)``
        Each row is ``[x_min, y_min, x_max, y_max]``.
    sanitize : bool, default False
        If ``True``, swap inverted coordinates where ``x_max < x_min`` or
        ``y_max < y_min``. If ``False``, raise ``ValueError`` for inverted boxes.

    Returns

    ndarray, shape ``(N, 4)``
        Each row is ``[x_min, y_min, width, height]``.

    Raises

    ValueError
        If ``sanitize=False`` and any box has ``x_max < x_min`` or ``y_max < y_min``.
    """
    boxes = np.asarray(boxes, dtype=np.float64)
    if boxes.ndim == 1:
        boxes = boxes.reshape(1, 4)
    if boxes.shape[0] == 0:
        return np.empty((0, 4), dtype=np.float64)

    inverted = (boxes[:, 2] < boxes[:, 0]) | (boxes[:, 3] < boxes[:, 1])
    if np.any(inverted):
        if not sanitize:
            msg = "Found inverted bounding box where x_max < x_min or y_max < y_min"
            raise ValueError(msg)
        boxes = boxes.copy()
        x_min = np.minimum(boxes[:, 0], boxes[:, 2])
        x_max = np.maximum(boxes[:, 0], boxes[:, 2])
        y_min = np.minimum(boxes[:, 1], boxes[:, 3])
        y_max = np.maximum(boxes[:, 1], boxes[:, 3])
        boxes[:, 0] = x_min
        boxes[:, 1] = y_min
        boxes[:, 2] = x_max
        boxes[:, 3] = y_max

    out = np.empty_like(boxes)
    out[:, 0] = boxes[:, 0]
    out[:, 1] = boxes[:, 1]
    out[:, 2] = boxes[:, 2] - boxes[:, 0]
    out[:, 3] = boxes[:, 3] - boxes[:, 1]
    return out

def xywh_to_xyxy(boxes: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
    """Convert ``(N, 4)`` boxes from xywh to xyxy format.

    Parameters

    boxes : ndarray, shape ``(N, 4)``
        Each row is ``[x_min, y_min, width, height]``.

    Returns

    ndarray, shape ``(N, 4)``
        Each row is ``[x_min, y_min, x_max, y_max]``.

    Raises

    ValueError
        If any box has negative width or height.
    """
    boxes = np.asarray(boxes, dtype=np.float64)
    if boxes.ndim == 1:
        boxes = boxes.reshape(1, 4)
    if boxes.shape[0] == 0:
        return np.empty((0, 4), dtype=np.float64)
    if np.any(boxes[:, 2] < 0) or np.any(boxes[:, 3] < 0):
        msg = "Box width and height must be non-negative"
        raise ValueError(msg)
    out = np.empty_like(boxes)
    out[:, 0] = boxes[:, 0]
    out[:, 1] = boxes[:, 1]
    out[:, 2] = boxes[:, 0] + boxes[:, 2]
    out[:, 3] = boxes[:, 1] + boxes[:, 3]
    return out

def xyxy_to_cxcywh(boxes: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
    """Convert ``(N, 4)`` boxes from xyxy to center-x/center-y/w/h format.

    Parameters

    boxes : ndarray, shape ``(N, 4)``
        Each row is ``[center_x, center_y, width, height]``.

    Returns

    ndarray, shape ``(N, 4)``
        Each row is ``[center_x, center_y, width, height]``.
    """
    boxes = np.asarray(boxes, dtype=np.float64)
    if boxes.ndim == 1:
        boxes = boxes.reshape(1, 4)
    if boxes.shape[0] == 0:
        return np.empty((0, 4), dtype=np.float64)
    out = np.empty_like(boxes)
    out[:, 2] = boxes[:, 2] - boxes[:, 0]  # width
    out[:, 3] = boxes[:, 3] - boxes[:, 1]  # height
    out[:, 0] = boxes[:, 0] + out[:, 2] / 2  # cx
    out[:, 1] = boxes[:, 1] + out[:, 3] / 2  # cy
    return out

def cxcywh_to_xyxy(boxes: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
    """Convert ``(N, 4)`` boxes from center-x/center-y/w/h to xyxy.

    Parameters

    boxes : ndarray, shape ``(N, 4)``
        Each row is ``[center_x, center_y, width, height]``.

    Returns

    ndarray, shape ``(N, 4)``
        Absolute-pixel xyxy boxes.
    """
    boxes = np.asarray(boxes, dtype=np.float64)
    if boxes.ndim == 1:
        boxes = boxes.reshape(1, 4)
    if boxes.shape[0] == 0:
        return np.empty((0, 4), dtype=np.float64)
    if np.any(boxes[:, 2] < 0) or np.any(boxes[:, 3] < 0):
        msg = "Box width and height must be non-negative"
        raise ValueError(msg)
    out = np.empty_like(boxes)
    half_w = boxes[:, 2] / 2
    half_h = boxes[:, 3] / 2
    out[:, 0] = boxes[:, 0] - half_w
    out[:, 1] = boxes[:, 1] - half_h
    out[:, 2] = boxes[:, 0] + half_w
    out[:, 3] = boxes[:, 1] + half_h
    return out

def xyxy_to_normalized(
    boxes: NDArray[np.floating[Any]],
    img_w: int,
    img_h: int,
) -> NDArray[np.floating[Any]]:
    """Convert ``(N, 4)`` xyxy boxes to normalized YOLO ``[cx, cy, w, h]``.

    Parameters

    boxes : ndarray, shape ``(N, 4)``
        Absolute-pixel xyxy boxes.
    img_w : int
        Image width in pixels.
    img_h : int
        Image height in pixels.

    Returns

    ndarray, shape ``(N, 4)``
        Normalized ``[cx, cy, w, h]`` in ``[0, 1]``.
    """
    if img_w <= 0 or img_h <= 0:
        msg = f"img_w and img_h must be positive integers, got img_w={img_w}, img_h={img_h}"
        raise ValueError(msg)
    boxes = np.asarray(boxes, dtype=np.float64)
    if boxes.ndim == 1:
        boxes = boxes.reshape(1, 4)
    if boxes.shape[0] == 0:
        return np.empty((0, 4), dtype=np.float64)
    cxcywh = xyxy_to_cxcywh(boxes)
    cxcywh[:, 0] /= img_w
    cxcywh[:, 1] /= img_h
    cxcywh[:, 2] /= img_w
    cxcywh[:, 3] /= img_h
    return cxcywh

def normalized_to_xyxy(
    boxes: NDArray[np.floating[Any]],
    img_w: int,
    img_h: int,
) -> NDArray[np.floating[Any]]:
    """Convert ``(N, 4)`` normalized YOLO boxes to absolute-pixel xyxy.

    Parameters

    boxes : ndarray, shape ``(N, 4)``
        Normalized ``[cx, cy, w, h]`` in ``[0, 1]``.
    img_w : int
        Image width in pixels.
    img_h : int
        Image height in pixels.

    Returns

    ndarray, shape ``(N, 4)``
        Absolute-pixel xyxy boxes.
    """
    if img_w <= 0 or img_h <= 0:
        msg = f"img_w and img_h must be positive integers, got img_w={img_w}, img_h={img_h}"
        raise ValueError(msg)
    boxes = np.asarray(boxes, dtype=np.float64)
    if boxes.ndim == 1:
        boxes = boxes.reshape(1, 4)
    if boxes.shape[0] == 0:
        return np.empty((0, 4), dtype=np.float64)
    denorm = boxes.copy()
    denorm[:, 0] *= img_w
    denorm[:, 1] *= img_h
    denorm[:, 2] *= img_w
    denorm[:, 3] *= img_h
    return cxcywh_to_xyxy(denorm)

# ---------------------------------------------------------------------------
# Dataset schema translators
# ---------------------------------------------------------------------------

def coco_to_yolo(
    coco: COCODataset,
    output_dir: Path | str,
    *,
    category_map: dict[int, int] | None = None,
) -> list[Path]:
    """Convert a COCO dataset to YOLO txt files.

    Parameters

    coco : COCODataset
        Validated COCO dataset.
    output_dir : Path | str
        Directory for output ``.txt`` files (one per image).
    category_map : dict[int, int] | None
        Optional mapping from COCO category ID → YOLO class index.
        If ``None``, a zero-based mapping is built from ``coco.categories``.

    Returns

    list[Path]
        Paths of the written label files.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if category_map is None:
        category_map = {c.id: i for i, c in enumerate(sorted(coco.categories, key=lambda c: c.id))}

    {img.id: img for img in coco.images}
    anns_by_image: dict[int, list[COCOAnnotation]] = {}
    for ann in coco.annotations:
        anns_by_image.setdefault(ann.image_id, []).append(ann)

    written: list[Path] = []
    for img in coco.images:
        stem = Path(img.file_name).stem
        txt_path = output_dir / f"{stem}.txt"
        lines: list[str] = []
        for ann in anns_by_image.get(img.id, []):
            cls_id = category_map.get(ann.category_id, ann.category_id)
            x, y, w, h = ann.bbox
            cx = (x + w / 2) / img.width
            cy = (y + h / 2) / img.height
            nw = w / img.width
            nh = h / img.height
            lines.append(f"{cls_id} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
        txt_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        written.append(txt_path)
    return written

def yolo_to_coco(
    label_dir: Path | str,
    image_sizes: dict[str, tuple[int, int]],
    category_names: Sequence[str],
) -> COCODataset:
    """Convert YOLO txt labels to a COCO dataset object.

    Parameters

    label_dir : Path | str
        Directory containing ``.txt`` label files.
    image_sizes : dict[str, tuple[int, int]]
        Mapping of image stem → ``(width, height)``; both dimensions must be positive.
    category_names : Sequence[str]
        Ordered list of category names (index = class id).

    Returns

    COCODataset
        Validated COCO dataset.
    """
    for stem, (width, height) in image_sizes.items():
        if width <= 0 or height <= 0:
            raise ValueError(f"Image {stem!r} must have positive width and height")
    label_dir = Path(label_dir)
    images: list[COCOImage] = []
    annotations: list[COCOAnnotation] = []
    categories = [COCOCategory(id=i, name=name) for i, name in enumerate(category_names)]

    ann_id = 1
    for img_id, (stem, (w, h)) in enumerate(sorted(image_sizes.items()), start=1):
        images.append(COCOImage(id=img_id, file_name=f"{stem}.jpg", width=w, height=h))
        txt_path = label_dir / f"{stem}.txt"
        if not txt_path.exists():
            continue
        for line in txt_path.read_text(encoding="utf-8").strip().splitlines():
            parts = line.strip().split()
            if len(parts) != 5:
                continue
            cls_id = int(parts[0])
            cx, cy, nw, nh = (float(p) for p in parts[1:])
            abs_w = nw * w
            abs_h = nh * h
            abs_x = cx * w - abs_w / 2
            abs_y = cy * h - abs_h / 2
            annotations.append(
                COCOAnnotation(
                    id=ann_id,
                    image_id=img_id,
                    category_id=cls_id,
                    bbox=[abs_x, abs_y, abs_w, abs_h],
                    area=abs_w * abs_h,
                )
            )
            ann_id += 1

    return COCODataset(images=images, annotations=annotations, categories=categories)

def coco_to_voc(
    coco: COCODataset,
    output_dir: Path | str,
) -> list[Path]:
    """Convert a COCO dataset to Pascal VOC XML files.

    Parameters

    coco : COCODataset
        Validated COCO dataset.
    output_dir : Path | str
        Directory for output ``.xml`` files.

    Returns

    list[Path]
        Paths of the written XML files.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cat_lookup: dict[int, str] = {c.id: c.name for c in coco.categories}
    anns_by_image: dict[int, list[COCOAnnotation]] = {}
    for ann in coco.annotations:
        anns_by_image.setdefault(ann.image_id, []).append(ann)

    written: list[Path] = []
    for img in coco.images:
        root = ET.Element("annotation")
        ET.SubElement(root, "filename").text = img.file_name

        size_el = ET.SubElement(root, "size")
        ET.SubElement(size_el, "width").text = str(img.width)
        ET.SubElement(size_el, "height").text = str(img.height)
        ET.SubElement(size_el, "depth").text = "3"

        for ann in anns_by_image.get(img.id, []):
            obj = ET.SubElement(root, "object")
            ET.SubElement(obj, "name").text = cat_lookup.get(ann.category_id, "unknown")
            ET.SubElement(obj, "difficult").text = "0"
            bndbox = ET.SubElement(obj, "bndbox")
            x, y, w, h = ann.bbox
            ET.SubElement(bndbox, "xmin").text = str(round(x))
            ET.SubElement(bndbox, "ymin").text = str(round(y))
            ET.SubElement(bndbox, "xmax").text = str(round(x + w))
            ET.SubElement(bndbox, "ymax").text = str(round(y + h))

        xml_str = minidom.parseString(ET.tostring(root, encoding="unicode")).toprettyxml(
            indent="  "
        )
        stem = Path(img.file_name).stem
        xml_path = output_dir / f"{stem}.xml"
        xml_path.write_text(xml_str, encoding="utf-8")
        written.append(xml_path)
    return written

def _safe_int(el: ET.Element | None, tag: str, default: int = 0) -> int:
    if el is None:
        return default
    child = el.find(tag)
    if child is None or child.text is None:
        return default
    try:
        return int(child.text)
    except ValueError:
        return default

def _safe_float(el: ET.Element | None, tag: str, default: float = 0.0) -> float:
    if el is None:
        return default
    child = el.find(tag)
    if child is None or child.text is None:
        return default
    try:
        return float(child.text)
    except ValueError:
        return default

def voc_to_coco(
    xml_dir: Path | str,
    category_names: Sequence[str],
) -> COCODataset:
    """Convert Pascal VOC XML files to a COCO dataset object.

    Parameters

    xml_dir : Path | str
        Directory containing ``.xml`` annotation files.
    category_names : Sequence[str]
        Ordered list of category names. The name in VOC ``<name>`` is looked up
        here to determine the ``category_id``.

    Returns

    COCODataset
        Validated COCO dataset.
    """
    xml_dir = Path(xml_dir)
    name_to_id = {name: i for i, name in enumerate(category_names)}
    categories = [COCOCategory(id=i, name=name) for i, name in enumerate(category_names)]

    images: list[COCOImage] = []
    annotations: list[COCOAnnotation] = []
    ann_id = 1

    for img_id, xml_path in enumerate(sorted(xml_dir.glob("*.xml")), start=1):
        tree = ET.parse(xml_path)
        root_el = tree.getroot()

        filename_el = root_el.find("filename")
        filename = filename_el.text if filename_el is not None and filename_el.text else ""

        size_el = root_el.find("size")
        w = _safe_int(size_el, "width")
        h = _safe_int(size_el, "height")

        images.append(COCOImage(id=img_id, file_name=filename, width=w, height=h))

        for obj in root_el.findall("object"):
            name_el = obj.find("name")
            name = name_el.text if name_el is not None and name_el.text else "unknown"
            cat_id = name_to_id.get(name, 0)

            bndbox = obj.find("bndbox")
            if bndbox is None:
                continue
            xmin = _safe_float(bndbox, "xmin")
            ymin = _safe_float(bndbox, "ymin")
            xmax = _safe_float(bndbox, "xmax")
            ymax = _safe_float(bndbox, "ymax")

            bw = xmax - xmin
            bh = ymax - ymin
            annotations.append(
                COCOAnnotation(
                    id=ann_id,
                    image_id=img_id,
                    category_id=cat_id,
                    bbox=[xmin, ymin, bw, bh],
                    area=bw * bh,
                )
            )
            ann_id += 1

    return COCODataset(images=images, annotations=annotations, categories=categories)

def voc_to_yolo(
    xml_dir: Path | str,
    output_dir: Path | str,
    category_names: Sequence[str],
) -> list[Path]:
    """Convert Pascal VOC XML annotations to YOLO txt labels.

    Parameters

    xml_dir : Path | str
        Directory containing ``.xml`` annotation files.
    output_dir : Path | str
        Directory for output ``.txt`` label files.
    category_names : Sequence[str]
        Ordered list of category names (index = class id).

    Returns

    list[Path]
        Paths of the written label files.
    """
    coco = voc_to_coco(xml_dir, category_names)
    return coco_to_yolo(coco, output_dir)

def yolo_to_voc(
    label_dir: Path | str,
    output_dir: Path | str,
    image_sizes: dict[str, tuple[int, int]],
    category_names: Sequence[str],
) -> list[Path]:
    """Convert YOLO txt labels to Pascal VOC XML annotations.

    Parameters

    label_dir : Path | str
        Directory containing ``.txt`` label files.
    output_dir : Path | str
        Directory for output ``.xml`` files.
    image_sizes : dict[str, tuple[int, int]]
        Mapping of image stem → ``(width, height)``.
    category_names : Sequence[str]
        Ordered list of category names (index = class id).

    Returns

    list[Path]
        Paths of the written XML files.
    """
    coco = yolo_to_coco(label_dir, image_sizes, category_names)
    return coco_to_voc(coco, output_dir)

def validate_coco_file(path: Path | str) -> COCODataset:
    """Load and validate a COCO JSON file.

    Parameters

    path : Path | str
        Path to the COCO JSON file.

    Returns

    COCODataset
        Validated COCO dataset.

    Raises

    pydantic.ValidationError
        If the JSON does not match the expected schema.
    """
    path = Path(path)
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return COCODataset(**data)
