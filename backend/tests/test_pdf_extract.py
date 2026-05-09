"""Tests for the pdf_extract tool.

Generates a native PDF with PyMuPDF on the fly so no binary fixture is committed.
OCR exercise is skipped if `tesseract` is not on PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from workflow_platform.tools import PdfExtractTool


def _make_native_pdf(path: Path, text: str) -> None:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


async def test_extract_native_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    _make_native_pdf(pdf_path, "Hello from the prototype. Invoice #12345.")
    tool = PdfExtractTool()

    result = await tool.execute({"filepath": str(pdf_path)})

    assert result.ok, result.error
    assert result.content["is_native"] is True
    assert "Invoice #12345" in result.content["text"]
    assert result.content["char_count"] > 0


async def test_extract_missing_file(tmp_path: Path) -> None:
    tool = PdfExtractTool()
    result = await tool.execute({"filepath": str(tmp_path / "does-not-exist.pdf")})

    assert not result.ok
    assert result.error is not None
    assert "not found" in result.error.lower()


async def test_extract_rejects_non_pdf(tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-pdf.txt"
    bogus.write_text("hello")
    tool = PdfExtractTool()

    result = await tool.execute({"filepath": str(bogus)})

    assert not result.ok
    assert result.error is not None
    assert "not a pdf" in result.error.lower()


async def test_extract_requires_filepath_param() -> None:
    tool = PdfExtractTool()
    result = await tool.execute({})

    assert not result.ok
    assert result.error is not None
    assert "filepath" in result.error.lower()


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
async def test_extract_scanned_pdf_uses_ocr(tmp_path: Path) -> None:
    """A page with no text layer should fall back to OCR."""
    import fitz

    pdf_path = tmp_path / "scanned.pdf"
    doc = fitz.open()
    doc.new_page()  # blank page = no native text
    doc.save(str(pdf_path))
    doc.close()

    tool = PdfExtractTool()
    result = await tool.execute({"filepath": str(pdf_path)})

    assert result.ok, result.error
    assert result.content["is_native"] is False


def test_tool_metadata_renders_for_bedrock() -> None:
    spec = PdfExtractTool().to_bedrock_tool_spec()
    assert spec["toolSpec"]["name"] == "pdf_extract"
    assert "filepath" in spec["toolSpec"]["inputSchema"]["json"]["properties"]
