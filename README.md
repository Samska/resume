# Samuel Andrade — Resume

[![Build resume PDFs](https://github.com/Samska/resume/actions/workflows/build-pdf.yml/badge.svg)](https://github.com/Samska/resume/actions/workflows/build-pdf.yml)

Version-controlled, ATS-friendly resumes written in Markdown and generated as PDF with GitHub Actions.

[Download — English (PDF)](pdf/Samuel-Andrade-Resume-en-US.pdf) · [Baixar — Português (PDF)](pdf/Samuel-Andrade-Resume-pt-BR.pdf)

The Markdown resumes are the source of truth; do not edit the PDFs directly.

## About

Software engineer focused on quality engineering, test automation, and reliable delivery across web, mobile, APIs, accessibility, and CI/CD.

## How it works

1. Edit the relevant Markdown file.
2. Push the change to `main`.
3. GitHub Actions rebuilds the Portuguese and English PDFs into [`pdf/`](pdf) and validates their structure, extractable text, reading order, and page count.
4. If validation passes, the workflow commits the updated PDFs.

You can also run the same workflow manually from **Actions → Build resume PDFs → Run workflow**.

This repository is intentionally limited to the Markdown-to-PDF workflow. It contains no vacancy analysis, tailored-resume generation, match reports, provenance manifests, or OpenRouter integration.

## Files

| File | Purpose |
| --- | --- |
| [RESUME_en-US.md](RESUME_en-US.md) | English resume source |
| [RESUME_pt-BR.md](RESUME_pt-BR.md) | Currículo em português |
| [pdf/](pdf) | Generated PDF files |
| [scripts/validate_resume.py](scripts/validate_resume.py) | Technical PDF validation |
| [.github/workflows/build-pdf.yml](.github/workflows/build-pdf.yml) | Markdown-to-PDF automation |

## Validation

Each workflow run generates both PDFs and validates readability, structure, identity and contact details, main sections, experience order, education, and the one-to-two-page limit. The generated PDFs are committed only when validation passes.
