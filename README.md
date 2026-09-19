# Samuel Andrade — Resume

Version-controlled, ATS-friendly resumes written in Markdown and automatically generated as PDF with GitHub Actions.

[Download — English (PDF)](pdf/Samuel-Andrade-Resume-en-US.pdf) · [Baixar — Português (PDF)](pdf/Samuel-Andrade-Resume-pt-BR.pdf)

## About

Software engineer focused on quality engineering, test automation, and reliable delivery across web, mobile, APIs, accessibility, and CI/CD.

## How it works

1. Edit the relevant Markdown file.
2. Push the change to `main`.
3. GitHub Actions rebuilds the PDFs and validates their text, structure, reading order, and page count.
4. If validation passes, the workflow commits the updated PDFs.

## Files

| File | Purpose |
| --- | --- |
| [RESUME_en-US.md](RESUME_en-US.md) | English resume source |
| [RESUME_pt-BR.md](RESUME_pt-BR.md) | Currículo em português |
| [pdf/](pdf) | Generated PDF files |
| [scripts/validate_resume.py](scripts/validate_resume.py) | Technical PDF validation |
| [.github/workflows/build-pdf.yml](.github/workflows/build-pdf.yml) | Markdown-to-PDF automation |

The Markdown files are the source of truth; do not edit the PDF files directly.

## Validation

Each workflow run publishes a technical validation report in its GitHub Actions summary. The report checks whether both PDFs are readable, contain the expected contact details and sections, preserve reverse-chronological experience order, include education, and stay within two pages.

The result is a build-quality check, not an ATS score or a job-match score. Relevance still depends on the requirements and keywords of each job description.
