#!/usr/bin/env python3
"""Validate the generated tailored resume before uploading it."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

IDENTITY = ("Samuel Andrade", "samuel.andradetp@live.com", "linkedin.com/in/Samska", "github.com/Samska")
EMPLOYERS = ("Trustly", "AB InBev", "CI&T", "e.Mix", "DNGX")


def command(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def check(condition: bool, message: str) -> tuple[bool, str]:
    return condition, message


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    text = command("pdftotext", "-layout", str(args.pdf), "-")
    info = command("pdfinfo", str(args.pdf))
    pages = int(next(line.split(":", 1)[1] for line in info.splitlines() if line.startswith("Pages:")))
    positions = [text.find(item) for item in EMPLOYERS]
    checks = [
        check(args.pdf.exists() and args.pdf.stat().st_size > 10_000, "PDF exists and is not empty"),
        check(1 <= pages <= 2, f"Page count is within the two-page limit ({pages})"),
        check(len(text.strip()) >= 1500, "PDF contains enough extractable text"),
        check(all(item in text for item in IDENTITY), "Identity and contact details are preserved"),
        check(all(item in text for item in EMPLOYERS), "All employers are preserved"),
        check(positions == sorted(positions), "Experience remains in reverse chronological order"),
        check(args.report.exists() and len(args.report.read_text(encoding="utf-8")) >= 200, "Match report was generated"),
    ]

    summary = ["## Tailored resume validation", "", "This is a technical quality check, not an ATS score.", ""]
    summary.extend(f"- {'PASS' if passed else 'FAIL'} — {message}" for passed, message in checks)
    summary_text = "\n".join(summary) + "\n"
    print(summary_text)
    if path := __import__("os").environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(summary_text)
            handle.write("\n## Advisory match report\n\n")
            handle.write(args.report.read_text(encoding="utf-8"))
    return 0 if all(passed for passed, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
