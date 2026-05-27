"""OCR text extraction for image files (JPG / PNG / WebP / TIFF / BMP).

Companion to `PdfExtractTool`. Where `pdf_extract` handles whole-document
PDFs (with native-text shortcut + OCR fallback), `image_ocr` is for the
case where the input is already an image — typically the output of a
`BrowserDownloadTool` call. Both rely on pytesseract; the platform
Dockerfile installs `tesseract-ocr` so the binary is present.

Kept separate so workflows can grant read-only image-OCR access without
also granting PDF parsing, and so the agent-facing tool surface matches
the user's mental model: "I downloaded a JPG — OCR it."
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

from workflow_platform.tools.base import Tool, ToolContext, ToolResult


class ImageOcrTool(Tool):
    name: ClassVar[str] = "image_ocr"
    description: ClassVar[str] = (
        "Run OCR (Tesseract) on a local image file (JPG / PNG / WebP / TIFF / BMP) "
        "and return the extracted text. Use this on files produced by browser_download "
        "or already-downloaded scanned-document images. For PDFs, use pdf_extract."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "Absolute path to the image file on the local filesystem.",
            },
            "lang": {
                "type": "string",
                "description": (
                    "Tesseract language code (default 'eng'). Use multi-lang like "
                    "'eng+fra' if needed. The container ships English data only by default."
                ),
            },
        },
        "required": ["filepath"],
    }

    SUPPORTED_SUFFIXES: ClassVar[tuple[str, ...]] = (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".tif",
        ".tiff",
        ".bmp",
    )

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        filepath = params.get("filepath")
        if not isinstance(filepath, str) or not filepath:
            return ToolResult(error="filepath is required")
        lang_raw = params.get("lang", "eng")
        if not isinstance(lang_raw, str) or not lang_raw:
            return ToolResult(error="lang must be a non-empty string")

        if (
            context is not None
            and context.capabilities is not None
            and not context.capabilities.can_read(filepath)
        ):
            return ToolResult(
                error=f"Capability denied: read {filepath!r} is outside file_read ACL"
            )

        path = Path(filepath)
        if not path.is_file():
            return ToolResult(error=f"File not found: {filepath}")
        suffix = path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            return ToolResult(
                error=(
                    f"Unsupported image type {suffix!r} — supported: "
                    f"{', '.join(self.SUPPORTED_SUFFIXES)}"
                )
            )

        try:
            text = await asyncio.to_thread(self._extract, str(path), lang_raw)
        except Exception as exc:
            return ToolResult(error=f"OCR failed: {exc}")

        return ToolResult(
            content={
                "text": text,
                "char_count": len(text),
                "lang": lang_raw,
            }
        )

    @staticmethod
    def _extract(filepath: str, lang: str) -> str:
        import pytesseract
        from PIL import Image

        with Image.open(filepath) as img:
            text: str = pytesseract.image_to_string(img, lang=lang)
        return text
