# Samuel Andrade Resume

[![Build and validate resume](https://github.com/Samska/resume/actions/workflows/build-resume.yml/badge.svg)](https://github.com/Samska/resume/actions/workflows/build-resume.yml)

Version-controlled, bilingual, and ATS-friendly resumes generated from structured content.

## Download

| Language | PDF | DOCX | Markdown | HTML |
| --- | --- | --- | --- | --- |
| English | [PDF](dist/Samuel-Andrade-Resume-en-US.pdf) | [DOCX](dist/Samuel-Andrade-Resume-en-US.docx) | [Markdown](dist/Samuel-Andrade-Resume-en-US.md) | [HTML](dist/Samuel-Andrade-Resume-en-US.html) |
| Português | [PDF](dist/Samuel-Andrade-Resume-pt-BR.pdf) | [DOCX](dist/Samuel-Andrade-Resume-pt-BR.docx) | [Markdown](dist/Samuel-Andrade-Resume-pt-BR.md) | [HTML](dist/Samuel-Andrade-Resume-pt-BR.html) |

## How it works

The editable content lives in `content/`. The build script produces stable PDF, DOCX, Markdown, and HTML files in `dist/`.

The GitHub Actions workflow:

- builds both language versions on every relevant change;
- verifies that PDF and DOCX outputs contain extractable text;
- checks expected sections, employers, roles, and education entries;
- rejects accidental publication of a phone number;
- uploads all generated files as a workflow artifact;
- updates the stable files in `dist/` after changes reach `main`.

## Edit and build locally

Requirements: Python 3.12 and GNU Make.

```bash
make setup
make validate
```

To build one language only:

```bash
python scripts/build.py --locale en-US
python scripts/build.py --locale pt-BR
```

## Repository structure

```text
content/                 Structured resume content by language
dist/                    Generated and recruiter-ready files
scripts/build.py         PDF, DOCX, Markdown, and HTML generator
scripts/validate.py      Content and ATS-readability checks
styles/resume.css        Browser and print preview styles
.github/workflows/       Automated build and validation
```

## Design principles

- single-column reading order;
- conventional section names;
- searchable and selectable text;
- no photos, icons, sidebars, skill charts, or text boxes;
- stable filenames for applications and profile links;
- public contact details limited to email and professional profiles.

## License

The automation code is available under the MIT License. Resume content and personal information remain copyright Samuel Andrade and may not be reused without permission. See [LICENSE](LICENSE).
