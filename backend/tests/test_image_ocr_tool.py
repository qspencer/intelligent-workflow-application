"""Tests for `ImageOcrTool` — the JPG/PNG OCR companion to PdfExtractTool.

Validation happens at the boundary (file existence + extension allowlist
+ capability ACL); the actual tesseract call is patched out so these
tests don't depend on the binary being present.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from workflow_platform.security.capabilities import CapabilityPolicy, ResolvedCapabilities
from workflow_platform.tools import ImageOcrTool, ToolContext


async def test_image_ocr_returns_text_on_success(tmp_path: Path) -> None:
    img = tmp_path / "invoice.jpg"
    img.write_bytes(b"\xff\xd8not-real-jpeg-bytes")
    with patch.object(
        ImageOcrTool, "_extract", staticmethod(lambda filepath, lang: "Invoice Total: 123.45")
    ):
        result = await ImageOcrTool().execute({"filepath": str(img)})
    assert result.ok
    assert result.content == {
        "text": "Invoice Total: 123.45",
        "char_count": 21,
        "lang": "eng",
    }


async def test_image_ocr_honors_lang_override(tmp_path: Path) -> None:
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nbytes")
    captured: dict[str, str] = {}

    def fake_extract(filepath: str, lang: str) -> str:
        captured["lang"] = lang
        return "extracted"

    with patch.object(ImageOcrTool, "_extract", staticmethod(fake_extract)):
        result = await ImageOcrTool().execute({"filepath": str(img), "lang": "eng+fra"})
    assert result.ok
    assert captured["lang"] == "eng+fra"
    assert result.content["lang"] == "eng+fra"


async def test_image_ocr_rejects_missing_filepath() -> None:
    result = await ImageOcrTool().execute({})
    assert not result.ok
    assert "filepath" in (result.error or "").lower()


async def test_image_ocr_rejects_nonexistent_file(tmp_path: Path) -> None:
    result = await ImageOcrTool().execute({"filepath": str(tmp_path / "missing.jpg")})
    assert not result.ok
    assert "not found" in (result.error or "").lower()


async def test_image_ocr_rejects_unsupported_extension(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-...")
    result = await ImageOcrTool().execute({"filepath": str(pdf)})
    assert not result.ok
    assert "unsupported" in (result.error or "").lower()


async def test_image_ocr_accepts_all_documented_extensions(tmp_path: Path) -> None:
    for suffix in ImageOcrTool.SUPPORTED_SUFFIXES:
        img = tmp_path / f"file{suffix}"
        img.write_bytes(b"\x00")
        with patch.object(ImageOcrTool, "_extract", staticmethod(lambda fp, lang: "text")):
            result = await ImageOcrTool().execute({"filepath": str(img)})
        assert result.ok, f"{suffix} should be supported"


async def test_image_ocr_capability_denied(tmp_path: Path) -> None:
    img = tmp_path / "blocked.jpg"
    img.write_bytes(b"\xff\xd8")
    # `file_read=[]` means *no* paths allowed — every read is denied.
    caps = ResolvedCapabilities(layers=[CapabilityPolicy(file_read=[])])
    ctx = ToolContext(capabilities=caps)
    result = await ImageOcrTool().execute({"filepath": str(img)}, context=ctx)
    assert not result.ok
    assert "capability denied" in (result.error or "").lower()


async def test_image_ocr_surfaces_tesseract_exception(tmp_path: Path) -> None:
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8")

    def boom(filepath: str, lang: str) -> str:
        raise RuntimeError("tesseract not installed")

    with patch.object(ImageOcrTool, "_extract", staticmethod(boom)):
        result = await ImageOcrTool().execute({"filepath": str(img)})
    assert not result.ok
    assert "tesseract not installed" in (result.error or "")


async def test_image_ocr_rejects_empty_lang(tmp_path: Path) -> None:
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8")
    result = await ImageOcrTool().execute({"filepath": str(img), "lang": ""})
    assert not result.ok
    assert "lang" in (result.error or "").lower()


def test_image_ocr_tool_name_and_schema_shape() -> None:
    assert ImageOcrTool.name == "image_ocr"
    schema: dict[str, Any] = ImageOcrTool.parameters_schema
    assert schema["type"] == "object"
    assert "filepath" in schema["properties"]
    assert "lang" in schema["properties"]
    assert schema["required"] == ["filepath"]
