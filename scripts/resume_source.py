#!/usr/bin/env python3
"""Shared primitives for the LinkedIn profile import feature.

This module is intentionally self-contained: it only contains the Markdown
structure validation, text normalization, and language detection helpers that
the LinkedIn importer needs. It does not contain vacancy analysis,
tailored-resume generation, match reports, or provenance manifests.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

SUPPORTED_LANGUAGES = ("en-US", "pt-BR")

EXPECTED_SECTIONS = {
    "pt-BR": ("Resumo Profissional", "Habilidades Técnicas", "Experiência Profissional", "Formação"),
    "en-US": ("Professional Summary", "Technical Skills", "Professional Experience", "Education"),
}

MONTHS = (
    "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
    "Fev|Abr|Mai|Ago|Set|Out|Dez"
)
DATE_RE = re.compile(
    rf"(?:{MONTHS})\s+\d{{4}}\s+-\s+(?:{MONTHS})\s+\d{{4}}",
    re.IGNORECASE,
)
YEAR_RANGE_RE = re.compile(r"\b(?:19|20)\d{2}\b.*\b(?:19|20)\d{2}\b")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^-\s+\S.*$")
SKILL_RE = re.compile(r"^\*\*([^*]+):\*\*\s+(.+?)\s*$")
ROLE_DATE_RE = re.compile(r"^\*\*([^*]+)\*\*\s+\|\s+(.+?)\s*$")
HTML_TAG_RE = re.compile(r"<[A-Za-z/][^>]*>")
FENCED_BLOCK_RE = re.compile(r"(?m)^\s*(?:```|~~~)")

PDF_FORMAT_CHARS = ("\u00ad", "\u200b", "\u200c", "\u200d", "\ufeff")
UNICODE_HYPHENS = ("\u2010", "\u2011")
SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.;:!?%)\]])")
SPACE_AFTER_OPENING_RE = re.compile(r"([(\[])\s+")
LINE_BREAK_HYPHEN_KEEP_RE = re.compile(r"-\s*\n\s*")
LINE_BREAK_HYPHEN_DROP_RE = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)")

V2_INVISIBLE_CHARS = ("\u00ad", "\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\u2060", "\ufeff")
FOLD_TRANSLATION = str.maketrans({
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2212": "-",
    "\u2044": "/",
    "\u2215": "/",
    "\\": "/",
})
RAW_TOKEN_RE = re.compile(r"[^\W_]+(?:[+#./][^\W_]+)*", re.UNICODE)
NUMERIC_FACT_RE = re.compile(
    r"(?<![0-9A-Za-zÀ-ÖØ-öø-ÿ])"
    r"(\d+(?:[.,]\d+)?)\+?"
    r"(?:\s*(%|[A-Za-zÀ-ÖØ-öø-ÿ]{1,20}))?"
    r"(?![0-9A-Za-zÀ-ÖØ-öø-ÿ])"
)
PERCENT_UNIT_ALIASES = frozenset({
    "%", "percent", "pct", "porcento", "porcentos", "porcentagem", "porcentagens",
})
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?:;|…—])\s+")

PT_STOPWORDS = frozenset([
    "a", "as", "o", "os", "ao", "aos", "um", "uma", "uns", "umas", "de", "da", "das", "do", "dos",
    "em", "no", "na", "nos", "nas", "por", "para", "com", "sem", "sob", "sobre", "entre", "que",
    "se", "nao", "nem", "mas", "ou", "e", "como", "mais", "menos", "muito", "muita", "muitos",
    "muitas", "ja", "ainda", "tambem", "ate", "apos", "antes", "depois", "quando", "onde", "qual",
    "quais", "seu", "sua", "seus", "suas", "meu", "minha", "meus", "minhas", "nosso", "nossa",
    "ele", "ela", "eles", "elas", "isso", "isto", "esse", "essa", "esses", "essas", "este", "esta",
    "estes", "estas", "aquele", "aquela", "foi", "foram", "ser", "sao", "era", "eram", "tem",
    "ter", "ha", "havia", "pelo", "pela", "pelos", "pelas", "num", "numa",
])
EN_STOPWORDS = frozenset([
    "the", "and", "for", "with", "that", "this", "from", "into", "are", "was", "were", "will",
    "would", "can", "could", "should", "have", "has", "had", "not", "but", "or", "our", "your",
    "their", "its", "it", "is", "be", "been", "being", "to", "of", "in", "on", "at", "by", "as",
    "an", "we", "you", "they", "he", "she", "i", "do", "does", "did", "than", "then", "when",
    "where", "which", "who", "whom", "whose", "what", "how", "all", "any", "both", "each", "few",
    "more", "most", "other", "some", "such", "only", "own", "same", "too", "very", "also",
    "across", "within", "without", "over", "under", "during", "through", "against", "between",
])
STOPWORDS = PT_STOPWORDS | EN_STOPWORDS

PT_LANGUAGE_MARKERS = frozenset([
    "experiencia", "experiencias", "formacao", "profissional", "profissionais", "atuacao",
    "conhecimento", "conhecimentos", "habilidade", "habilidades", "idioma", "idiomas", "ensino",
    "graduacao", "certificacao", "certificacoes", "desenvolvimento", "analise", "gestao",
    "equipe", "equipes", "projeto", "projetos", "resultado", "resultados", "responsavel",
    "atualmente", "empresa", "empresas", "cargo", "cargos", "trabalho", "trabalhei", "atuei",
    "nacionalidade", "brasileiro", "brasileira", "acesso", "contato", "competencias",
    "realizacoes", "resumo", "objetivo", "formado", "cursando", "conclusao", "inicio",
])
EN_LANGUAGE_MARKERS = frozenset([
    "experience", "experiences", "education", "skills", "professional", "development",
    "analysis", "management", "team", "teams", "project", "projects", "results", "responsible",
    "currently", "years", "months", "engineering", "engineer", "software", "quality", "summary",
    "certification", "certifications", "languages", "university", "degree", "company", "work",
    "worked", "built", "led", "using", "achievements", "objective", "graduated", "contact",
    "profile", "about",
])
PT_DIACRITIC_RE = re.compile(r"[ãõçáéíóúâêôàèìòù]", re.IGNORECASE)
LANGUAGE_MARGIN = 4
LANGUAGE_DIACRITIC_BONUS = 3
LANGUAGE_DIACRITIC_THRESHOLD = 8


class ResumeImportError(ValueError):
    """An actionable, fail-closed LinkedIn import error."""

    def __init__(self, category: str, message: str):
        super().__init__(f"{category}: {message}")
        self.category = category
        self.reason = message


def _fail(category: str, message: str) -> None:
    raise ResumeImportError(category, message)


# ---------------------------------------------------------------------------
# Text normalization primitives.
# ---------------------------------------------------------------------------


def _strip_invisibles(text: str) -> str:
    for char in V2_INVISIBLE_CHARS:
        text = text.replace(char, "")
    return text


def _normalize_token(token: str) -> str:
    value = unicodedata.normalize("NFKD", token.casefold())
    return "".join(char for char in value if not unicodedata.combining(char))


def _normalize_tokens(text: str) -> tuple[str, ...]:
    value = unicodedata.normalize("NFKC", _strip_invisibles(text)).translate(FOLD_TRANSLATION)
    tokens: list[str] = []
    for match in RAW_TOKEN_RE.finditer(value):
        token = _normalize_token(match.group(0))
        if token:
            tokens.append(token)
    return tuple(tokens)


def _token_variants(token: str) -> frozenset[str]:
    variants = {token}
    if len(token) >= 5 and token.endswith("ies"):
        variants.add(token[:-3] + "y")
    if len(token) >= 6 and token.endswith("es") and not token.endswith("ses"):
        variants.add(token[:-2])
    if len(token) >= 5 and token.endswith("s") and not token.endswith("ss"):
        variants.add(token[:-1])
    return frozenset(variants)


def _normalize_numeric_text(text: str) -> str:
    normalized = _strip_invisibles(unicodedata.normalize("NFKC", text))
    return "".join(char for char in normalized if unicodedata.category(char)[0] != "C" or char in "\t\n\r")


def _numeric_unit(token: str) -> str:
    candidate = _normalize_token(token)
    if _token_variants(candidate) & PERCENT_UNIT_ALIASES:
        return "%"
    return candidate


def _numeric_facts(text: str) -> tuple[tuple[str, str | None], ...]:
    facts: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for match in NUMERIC_FACT_RE.finditer(_normalize_numeric_text(text)):
        value = match.group(1).replace(",", ".")
        unit: str | None = None
        if match.group(2):
            unit = _numeric_unit(match.group(2))
        fact = (value, unit)
        if fact not in seen:
            seen.add(fact)
            facts.append(fact)
    return tuple(facts)


def _raw_tokens(text: str) -> tuple[tuple[str, bool], ...]:
    value = _strip_invisibles(unicodedata.normalize("NFKC", text))
    tokens: list[tuple[str, bool]] = []
    for segment in SENTENCE_SPLIT_RE.split(value):
        first = True
        for match in RAW_TOKEN_RE.finditer(segment):
            tokens.append((match.group(0), first))
            first = False
    return tuple(tokens)


def _is_pattern_token(token: str) -> bool:
    if any(char in token for char in "+#./"):
        return True
    if any(char.isdigit() for char in token):
        return True
    letters = [char for char in token if char.isalpha()]
    if not letters:
        return False
    uppercase = sum(1 for char in letters if char.isupper())
    lowercase = sum(1 for char in letters if char.islower())
    if uppercase >= 2 and lowercase == 0:
        return True
    if uppercase >= 1 and lowercase >= 1 and any(char.isupper() for char in token[1:]):
        return True
    return False


def _collapse_extracted_whitespace(text: str) -> str:
    value = re.sub(r"\s+", " ", text)
    value = SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", value)
    value = SPACE_AFTER_OPENING_RE.sub(r"\1", value)
    return value.strip()


def _canonical_extracted(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    value = "\n".join(value.splitlines())
    for char in PDF_FORMAT_CHARS:
        value = value.replace(char, "")
    for char in UNICODE_HYPHENS:
        value = value.replace(char, "-")
    return value


def normalize_extracted(text: str) -> str:
    """Normalize extracted text for exact evidence matching."""

    return _collapse_extracted_whitespace(_canonical_extracted(text))


def extracted_text_variants(text: str) -> tuple[str, ...]:
    """Return deterministic normalized variants of PDF-extracted text."""

    canonical = _canonical_extracted(text)
    preserved = _collapse_extracted_whitespace(LINE_BREAK_HYPHEN_KEEP_RE.sub("-", canonical))
    joined = _collapse_extracted_whitespace(LINE_BREAK_HYPHEN_DROP_RE.sub("", canonical))
    variants = [preserved]
    if joined != preserved:
        variants.append(joined)
    return tuple(variants)


# ---------------------------------------------------------------------------
# Strict JSON contract helpers.
# ---------------------------------------------------------------------------


def _strict_json(raw: str) -> dict[str, Any]:
    candidate = raw.strip()
    if candidate.startswith("```"):
        fenced = re.fullmatch(
            r"```(?:json)?\s*\n?(.*?)\n?```", candidate, flags=re.IGNORECASE | re.DOTALL
        )
        if not fenced:
            _fail("MODEL_RESPONSE", "fenced response is not one complete JSON object")
        candidate = fenced.group(1).strip()
    elif "```" in candidate:
        _fail("MODEL_RESPONSE", "response contains surrounding prose or code fences")
    elif not (candidate.startswith("{") and candidate.endswith("}")):
        _fail("MODEL_RESPONSE", "response contains surrounding prose")
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        _fail("MODEL_RESPONSE", f"invalid JSON: {exc.msg}")
    if not isinstance(value, dict):
        _fail("MODEL_RESPONSE", "response must be a JSON object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    if set(value) != expected:
        unknown = sorted(set(value) - expected)
        missing = sorted(expected - set(value))
        detail = []
        if unknown:
            detail.append("unknown: " + ", ".join(unknown))
        if missing:
            detail.append("missing: " + ", ".join(missing))
        _fail("MODEL_SCHEMA", f"{context} fields are invalid ({'; '.join(detail)})")


# ---------------------------------------------------------------------------
# Deterministic language detection.
# ---------------------------------------------------------------------------


def detect_source_language(text: str) -> str | None:
    """Detect the dominant language of extracted text without a model.

    Returns ``None`` when the signal is weak or ambiguous so the caller can
    defer to the model declaration instead of guessing.
    """

    tokens = _normalize_tokens(text)
    portuguese = sum(1 for token in tokens if token in PT_LANGUAGE_MARKERS)
    english = sum(1 for token in tokens if token in EN_LANGUAGE_MARKERS)
    if len(PT_DIACRITIC_RE.findall(text)) >= LANGUAGE_DIACRITIC_THRESHOLD:
        portuguese += LANGUAGE_DIACRITIC_BONUS
    if portuguese == english:
        return None
    language = "pt-BR" if portuguese > english else "en-US"
    stronger = max(portuguese, english)
    weaker = min(portuguese, english)
    if stronger >= LANGUAGE_MARGIN and stronger >= weaker * 2:
        return language
    return None


# ---------------------------------------------------------------------------
# Master-resume structure validation.
# ---------------------------------------------------------------------------


def validate_master_structure(text: str, language: str) -> None:
    """Validate one master resume without modifying it.

    The importer only ever adds validated content, so this validator fails
    closed when the current structure would make deterministic rendering or
    ATS-friendly output unsafe.
    """

    if language not in EXPECTED_SECTIONS:
        _fail("SOURCE_PARSE", f"unsupported language: {language}")
    if not text.strip():
        _fail("SOURCE_PARSE", "source Markdown is empty")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or not re.fullmatch(r"#\s+.+", lines[0]):
        _fail("SOURCE_PARSE", "first line must be one identity heading")
    if FENCED_BLOCK_RE.search(normalized):
        _fail("SOURCE_PARSE", "master resume contains a fenced code block")
    if HTML_TAG_RE.search(normalized):
        _fail("SOURCE_PARSE", "master resume contains raw HTML")

    sections: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match and match.group(1) == "##":
            sections.append((index, match.group(2)))
    expected = EXPECTED_SECTIONS[language]
    titles = [title for _index, title in sections]
    if titles != list(expected):
        _fail(
            "SOURCE_PARSE",
            "master resume sections must be exactly, in order: " + ", ".join(expected),
        )

    bounds = [index for index, _title in sections] + [len(lines)]
    summary_lines = [line for line in lines[bounds[0] + 1 : bounds[1]] if line.strip()]
    if not summary_lines:
        _fail("SOURCE_PARSE", "master resume summary is empty")

    skill_lines = [line for line in lines[bounds[1] + 1 : bounds[2]] if line.strip()]
    if not skill_lines:
        _fail("SOURCE_PARSE", "master resume technical skills are empty")
    for line in skill_lines:
        if SKILL_RE.match(line) is None:
            _fail("SOURCE_PARSE", "master resume skill line is malformed")

    experience_lines = [line for line in lines[bounds[2] + 1 : bounds[3]] if line.strip()]
    if not any(line.startswith("### ") for line in experience_lines):
        _fail("SOURCE_PARSE", "master resume experience is empty")
    if not any(DATE_RE.search(line) for line in experience_lines):
        _fail("SOURCE_PARSE", "master resume experience has no dated entries")
    for line in experience_lines:
        if line.startswith("- ") and BULLET_RE.match(line) is None:
            _fail("SOURCE_PARSE", "master resume bullet is malformed")
        if line.startswith("**") and ROLE_DATE_RE.match(line) is None:
            _fail("SOURCE_PARSE", "master resume role line is malformed")

    education_blocks = re.split(r"\n[ \t]*\n", "\n".join(lines[bounds[3] + 1 :]))
    education_blocks = [block for block in education_blocks if block.strip()]
    if not education_blocks:
        _fail("SOURCE_PARSE", "master resume education is empty")
    for block in education_blocks:
        block_lines = [line for line in block.split("\n") if line.strip()]
        if not block_lines[0].startswith("### "):
            _fail("SOURCE_PARSE", "master resume education entry is malformed")
        if len(block_lines) != 2:
            _fail("SOURCE_PARSE", "master resume education entry is malformed")
        if not DATE_RE.search(block_lines[1]) and not YEAR_RANGE_RE.search(block_lines[1]):
            _fail("SOURCE_PARSE", "master resume education entry has no dates")
