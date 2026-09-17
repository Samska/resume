#!/usr/bin/env python3
"""Validate source parity and ATS-readable build outputs."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from docx import Document
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
CONTENT = ROOT / "content"


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def expected_tokens(data: dict) -> list[str]:
    tokens = [data["name"]]
    tokens.extend(data["section_labels"].values())
    tokens.extend(item["company"] for item in data["experience"])
    tokens.extend(item["role"] for item in data["experience"])
    tokens.extend(item["institution"] for item in data["education"])
    return tokens


def validate(locale: str) -> None:
    source = json.loads((CONTENT / f"resume.{locale}.json").read_text(encoding="utf-8"))
    stem = f"Samuel-Andrade-Resume-{locale}"
    required = [DIST / f"{stem}.{suffix}" for suffix in ("md", "html", "pdf", "docx")]
    missing = [str(path) for path in required if not path.exists() or path.stat().st_size == 0]
    if missing:
        raise AssertionError(f"missing or empty outputs: {missing}")

    pdf_reader = PdfReader(str(DIST / f"{stem}.pdf"))
    if not 1 <= len(pdf_reader.pages) <= 3:
        raise AssertionError(f"unexpected PDF page count for {locale}: {len(pdf_reader.pages)}")
    pdf_text = normalized("\n".join(page.extract_text() or "" for page in pdf_reader.pages))
    doc = Document(DIST / f"{stem}.docx")
    docx_text = normalized("\n".join(paragraph.text for paragraph in doc.paragraphs))
    md_text = normalized((DIST / f"{stem}.md").read_text(encoding="utf-8"))

    for token in expected_tokens(source):
        needle = normalized(token)
        for kind, haystack in (("PDF", pdf_text), ("DOCX", docx_text), ("Markdown", md_text)):
            if needle not in haystack:
                raise AssertionError(f"{locale} {kind}: missing expected text: {token}")
    if "+55" in pdf_text or "+55" in docx_text:
        raise AssertionError(f"{locale}: public output unexpectedly contains a phone number")
    print(f"validated {locale}: {len(pdf_reader.pages)} PDF page(s)")


def main() -> None:
    for locale in ("pt-BR", "en-US"):
        validate(locale)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        raise
