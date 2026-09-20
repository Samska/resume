#!/usr/bin/env python3
"""Validate source-grounded Markdown, match reports, and tailored PDFs."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

try:
    from scripts.resume_grounding import (
        GroundingError,
        load_manifest,
        normalize_extracted,
        parse_source,
        plain_markdown,
        validate_manifest,
        validate_rendered_markdown,
        validate_rendered_report,
    )
except ModuleNotFoundError:
    from resume_grounding import (  # type: ignore[no-redef]
        GroundingError,
        load_manifest,
        normalize_extracted,
        parse_source,
        plain_markdown,
        validate_manifest,
        validate_rendered_markdown,
        validate_rendered_report,
    )


def command(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def validate_pdf(pdf: Path, source, selection) -> list[tuple[bool, str]]:
    if not pdf.exists() or pdf.stat().st_size <= 10_000:
        return [(False, "PDF exists and is not empty")]
    try:
        text = command("pdftotext", "-layout", str(pdf), "-")
        info = command("pdfinfo", str(pdf))
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        return [(False, f"PDF tools could not validate the file: {exc}")]
    page_match = re.search(r"^Pages:\s+(\d+)$", info, re.MULTILINE)
    pages = int(page_match.group(1)) if page_match else 0
    normalized_pdf = normalize_extracted(text)
    required_fragments = [fragment for fragment in source.fragments.values() if fragment.mandatory]
    selected_fragments = [source.fragments[item] for item in selection.selected_fragment_ids]
    missing = [
        fragment.id
        for fragment in (*required_fragments, *selected_fragments)
        if normalize_extracted(plain_markdown(fragment.text)) not in normalized_pdf
    ]
    employer_positions = [normalized_pdf.find(normalize_extracted(employer.name)) for employer in source.employers]
    return [
        (1 <= pages <= 2, f"Page count is within the two-page limit ({pages})"),
        (len(text.strip()) >= 1500, "PDF contains enough extractable text"),
        (all(normalize_extracted(plain_markdown(fragment.text)) in normalized_pdf for fragment in required_fragments), "Mandatory source facts are preserved"),
        (not missing, "Selected source evidence is present in the PDF" if not missing else "Missing source fragments: " + ", ".join(missing)),
        (all(position >= 0 for position in employer_positions), "All employers are preserved"),
        (employer_positions == sorted(employer_positions), "Experience remains in reverse chronological order"),
    ]


def write_summary(lines: list[str]) -> None:
    summary_text = "\n".join(lines) + "\n"
    print(summary_text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(summary_text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--language", required=True, choices=("pt-BR", "en-US"))
    args = parser.parse_args()

    source = parse_source(args.source.read_text(encoding="utf-8"), args.language)
    selection = validate_manifest(source, load_manifest(args.manifest))
    markdown = args.markdown.read_text(encoding="utf-8")
    report = args.report.read_text(encoding="utf-8")
    validate_rendered_markdown(source, selection, markdown)
    validate_rendered_report(source, selection, report)
    pdf_checks = validate_pdf(args.pdf, source, selection)

    checks = [
        (True, "Source and temporary manifest agree"),
        (True, "Rendered Markdown is exact deterministic source composition"),
        (True, "Match report is exact deterministic template composition"),
        *pdf_checks,
    ]
    passed = all(result for result, _ in checks)
    lines = [
        "## Tailored resume validation",
        "",
        "This is a deterministic source-grounding and document-quality check, not an ATS score.",
        "",
    ]
    lines.extend(f"- {'PASS' if result else 'FAIL'} — {message}" for result, message in checks)
    lines.extend(
        [
            "",
            f"- Grounding statistics — selected fragments: {len(selection.selected_fragment_ids)}; "
            f"strong matches: {len(selection.strong_matches)}; partial matches: {len(selection.partial_matches)}; "
            f"gaps: {len(selection.gaps)}; employers: {len(source.employers)}",
        ]
    )
    write_summary(lines)
    return 0 if passed else 1
if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GroundingError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=__import__("sys").stderr)
        raise SystemExit(1)
