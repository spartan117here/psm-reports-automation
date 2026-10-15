"""
Report image generation and PDF-to-PNG rendering utilities.

Converts Google Sheets PDF exports into high-resolution, cropped, presentation-ready PNG cards.
Preserves existing ReportImageRenderer abstractions.
"""

from abc import ABC, abstractmethod
import logging
from pathlib import Path
from typing import List, Optional, Tuple, Union
from PIL import Image, ImageChops
import pymupdf

from app.reporting.consolidation import ConsolidatedReportData

logger = logging.getLogger("pothys_reporting")


def crop_white_margins(img: Image.Image, padding: int = 20) -> Image.Image:
    """
    Tightly crop white/empty margins around table content with configurable padding.

    Args:
        img: PIL Image object.
        padding: Pixel padding to preserve around non-white content bounding box.

    Returns:
        Cropped PIL Image object.
    """
    # Ensure RGB for background comparison
    rgb_img = img.convert("RGB")
    bg = Image.new("RGB", rgb_img.size, (255, 255, 255))
    diff = ImageChops.difference(rgb_img, bg)
    bbox = diff.getbbox()

    if not bbox:
        return img

    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(img.width, bbox[2] + padding)
    bottom = min(img.height, bbox[3] + padding)

    return img.crop((left, top, right, bottom))


def pdf_to_cropped_png(
    pdf_source: Union[bytes, str, Path],
    output_path: Union[str, Path],
    dpi: int = 200,
    padding: int = 20,
    auto_crop: bool = True,
) -> Tuple[Path, Tuple[int, int]]:
    """
    Render PDF content (bytes or file path) to a crisp, high-resolution PNG image.

    Args:
        pdf_source: PDF content as bytes, or filepath string / Path object.
        output_path: Target PNG destination path.
        dpi: DPI resolution for rendering (default: 200 for presentation clarity).
        padding: Pixel padding around the cropped table.
        auto_crop: Whether to crop whitespace margins around table.

    Returns:
        Tuple of (saved_file_path, (width, height)).
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(pdf_source, (str, Path)):
        doc = pymupdf.open(str(pdf_source))
    elif isinstance(pdf_source, bytes):
        if not pdf_source:
            raise ValueError("Cannot render PNG from empty PDF document.")
        doc = pymupdf.open(stream=pdf_source, filetype="pdf")
    else:
        raise TypeError(f"Unsupported pdf_source type: {type(pdf_source)}")

    if len(doc) == 0:
        raise ValueError("Cannot render PNG from empty PDF document.")

    # Render first page at target DPI
    page = doc[0]
    pix = page.get_pixmap(dpi=dpi)

    mode = "RGB" if pix.alpha == 0 else "RGBA"
    pil_img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)

    if auto_crop:
        pil_img = crop_white_margins(pil_img, padding=padding)

    pil_img.save(out_file, "PNG")
    dimensions = (pil_img.width, pil_img.height)
    logger.info(f"Rendered report screenshot to {out_file} ({dimensions[0]}x{dimensions[1]})")
    return out_file, dimensions


class ReportImageRenderer(ABC):
    """Abstract interface for rendering reports to image files."""

    @abstractmethod
    def render_new_enrollment_card(
        self, data: ConsolidatedReportData, output_path: Path
    ) -> Path:
        """Render store-wise New Enrollment metrics image card."""
        pass

    @abstractmethod
    def render_backlog_card(
        self, data: ConsolidatedReportData, output_path: Path
    ) -> Path:
        """Render Target vs Achieved Backlog metrics image card."""
        pass


class PlaceholderImageRenderer(ReportImageRenderer):
    """Placeholder image renderer for Phase 0."""

    def render_new_enrollment_card(
        self, data: ConsolidatedReportData, output_path: Path
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Create a text placeholder file representing the image
        output_path.write_text(
            f"IMAGE PLACEHOLDER: New Enrollment as of {data.report_date}\nTotal: {data.total_enrollment_count} enrollments",
            encoding="utf-8",
        )
        return output_path

    def render_backlog_card(
        self, data: ConsolidatedReportData, output_path: Path
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(f"IMAGE PLACEHOLDER: Backlog as of {data.report_date}", encoding="utf-8")
        return output_path
