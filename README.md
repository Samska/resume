# Samuel Andrade — Resume

[![Build resume PDFs](https://github.com/Samska/resume/actions/workflows/build-pdf.yml/badge.svg)](https://github.com/Samska/resume/actions/workflows/build-pdf.yml)

## Source of truth

The Markdown resumes are the source of truth. Do not edit the generated PDFs directly.

- [RESUME_en-US.md](RESUME_en-US.md) — English
- [RESUME_pt-BR.md](RESUME_pt-BR.md) — Brazilian Portuguese

## PDF generation

The [build-pdf](.github/workflows/build-pdf.yml) GitHub Actions workflow generates both PDFs from the Markdown resumes:

- automatically on a push that changes `RESUME_*.md` or the workflow;
- manually from **Actions → Build resume PDFs → Run workflow** (`workflow_dispatch`).

Generated PDFs are stored under [`pdf/`](pdf):

- [pdf/Samuel-Andrade-Resume-en-US.pdf](pdf/Samuel-Andrade-Resume-en-US.pdf)
- [pdf/Samuel-Andrade-Resume-pt-BR.pdf](pdf/Samuel-Andrade-Resume-pt-BR.pdf)
