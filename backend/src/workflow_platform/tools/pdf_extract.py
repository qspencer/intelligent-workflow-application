"""PDF text extraction with OCR fallback.

Ported from the prototype `services/pdf_extractor.py`. Behavior preserved:
PyMuPDF for native text; pytesseract via pdf2image for scanned documents.
A page is treated as native if any single page contains more than `threshold`
characters of native text.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

from workflow_platform.tools.base import Tool, ToolContext, ToolResult


class PdfExtractTool(Tool):
    name: ClassVar[str] = "pdf_extract"
    description: ClassVar[str] = (
        "Extract text from a PDF file. Auto-detects native vs. scanned pages and "
        "falls back to OCR for scanned documents. Returns the extracted text and "
        "whether the document was native or required OCR."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "Absolute path to the PDF file on the local filesystem.",
            }
        },
        "required": ["filepath"],
    }

    NATIVE_THRESHOLD: ClassVar[int] = 30

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        filepath = params.get("filepath")
        if not isinstance(filepath, str) or not filepath:
            return ToolResult(error="filepath is required")
        path = Path(filepath)
        if not path.is_file():
            return ToolResult(error=f"File not found: {filepath}")
        if path.suffix.lower() != ".pdf":
            return ToolResult(error=f"Not a PDF: {filepath}")

        try:
            text, is_native = await asyncio.to_thread(self._extract, str(path))
        except Exception as exc:
            return ToolResult(error=f"Extraction failed: {exc}")

        return ToolResult(
            content={
                "text": text,
                "is_native": is_native,
                "char_count": len(text),
            }
        )

    @classmethod
    def _extract(cls, filepath: str) -> tuple[str, bool]:
        if cls._is_native(filepath):
            return cls._extract_native(filepath), True
        return cls._extract_ocr(filepath), False

    @classmethod
    def _is_native(cls, filepath: str) -> bool:
        import fitz

        doc = fitz.open(filepath)
        try:
            return any(len(page.get_text().strip()) > cls.NATIVE_THRESHOLD for page in doc)
        finally:
            doc.close()

    @staticmethod
    def _extract_native(filepath: str) -> str:
        import fitz

        doc = fitz.open(filepath)
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()

    @staticmethod
    def _extract_ocr(filepath: str) -> str:
        import pytesseract
        from pdf2image import convert_from_path

        images = convert_from_path(filepath)
        return "\n".join(pytesseract.image_to_string(img) for img in images)
