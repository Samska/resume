#!/usr/bin/env python3
"""Deterministic source grounding for tailored resumes and match reports."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
MAX_REQUIREMENTS = 20
MAX_SELECTED_FRAGMENTS = 25
MAX_SUMMARY_FRAGMENTS = 2
MAX_BULLETS_PER_EMPLOYER = 4
MAX_TOTAL_BULLETS = 16
MAX_VACANCY_TEXT = 300
MAX_TARGET_TEXT = 120

SCHEMA_VERSION_V2 = 2
MAX_HEADLINE_CHARS = 160
MIN_HEADLINE_CHARS = 5
MAX_SUMMARY_BLOCKS = 2
MIN_SUMMARY_CHARS = 20
MAX_SUMMARY_CHARS = 600
MAX_SKILL_GROUPS = 12
MAX_SKILL_ITEMS = 30
MAX_SKILL_ITEM_CHARS = 80
MAX_SKILL_GROUP_CHARS = 300
MIN_BULLET_CHARS = 10
MAX_BULLET_CHARS = 280
MAX_PROVENANCE_PER_BLOCK = 4
MAX_REQUIREMENT_LINKS_PER_BLOCK = 6
MAX_TOTAL_CANDIDATE_CHARS = 8000
MAX_CLASSIFICATION_EVIDENCE = 8
MAX_CLASSIFICATION_EVIDENCE_TOTAL = 60
LENGTH_REVIEW_BULLET_CHARS = 260
RESUME_LENGTH_REVIEW_CHARS = 6500
MIN_TOTAL_BULLETS_TARGET = 12
NEAR_DUPLICATE_RATIO = 0.9

MONTHS = (
    "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
    "Fev|Abr|Mai|Ago|Set|Out|Dez"
)
DATE_RE = re.compile(
    rf"(?:{MONTHS})\s+\d{{4}}\s+-\s+(?:{MONTHS})\s+\d{{4}}",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^-\s+\S.*$")
SKILL_RE = re.compile(r"^\*\*([^*]+):\*\*\s+(.+?)\s*$")
ROLE_DATE_RE = re.compile(r"^\*\*([^*]+)\*\*\s+\|\s+(.+?)\s*$")
REQUIREMENT_ID_RE = re.compile(r"^req-[a-z0-9][a-z0-9-]{0,39}$")
PDF_FORMAT_CHARS = ("\u00ad", "\u200b", "\u200c", "\u200d", "\ufeff")
UNICODE_HYPHENS = ("\u2010", "\u2011")
SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.;:!?%)\]])")
SPACE_AFTER_OPENING_RE = re.compile(r"([(\[])\s+")
LINE_BREAK_HYPHEN_KEEP_RE = re.compile(r"-\s*\n\s*")
LINE_BREAK_HYPHEN_DROP_RE = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)")


class GroundingError(ValueError):
    """An actionable deterministic grounding error."""

    def __init__(self, category: str, message: str):
        super().__init__(f"{category}: {message}")
        self.category = category
        self.reason = message


@dataclass(frozen=True)
class Fragment:
    id: str
    kind: str
    section: str
    owner: str | None
    role: str | None
    text: str
    order: int
    mandatory: bool
    selectable: bool


@dataclass(frozen=True)
class Employer:
    key: str
    name: str
    fragment_ids: tuple[str, ...]
    bullet_ids: tuple[str, ...]
    header_groups: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Education:
    key: str
    fragment_ids: tuple[str, ...]


@dataclass(frozen=True)
class SourceResume:
    language: str
    text: str
    digest: str
    fragments: dict[str, Fragment]
    section_ids: dict[str, str]
    preamble_ids: tuple[str, ...]
    summary_ids: tuple[str, ...]
    skill_ids: tuple[str, ...]
    spoken_language_id: str
    employers: tuple[Employer, ...]
    education: tuple[Education, ...]


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    priority: str


@dataclass(frozen=True)
class Match:
    requirement_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ValidatedSelection:
    schema_version: int
    language: str
    source_digest: str
    target_company: str
    target_role: str
    selected_fragment_ids: tuple[str, ...]
    requirements: tuple[Requirement, ...]
    strong_matches: tuple[Match, ...]
    partial_matches: tuple[Match, ...]
    gaps: tuple[str, ...]
    interview_topics: tuple[str, ...]

    def to_manifest(self) -> dict[str, Any]:
        classifications: dict[str, dict[str, Any]] = {}
        for match in self.strong_matches:
            classifications[match.requirement_id] = {
                "status": "strong",
                "evidence_ids": list(match.evidence_ids),
            }
        for match in self.partial_matches:
            classifications[match.requirement_id] = {
                "status": "partial",
                "evidence_ids": list(match.evidence_ids),
            }
        for requirement_id in self.gaps:
            classifications[requirement_id] = {"status": "gap", "evidence_ids": []}

        return {
            "schema_version": self.schema_version,
            "language": self.language,
            "source_digest": self.source_digest,
            "target_company": self.target_company,
            "target_role": self.target_role,
            "selected_fragment_ids": list(self.selected_fragment_ids),
            "vacancy_requirements": [
                {"id": item.id, "text": item.text, "priority": item.priority}
                for item in self.requirements
            ],
            "classifications": classifications,
            "interview_topics": list(self.interview_topics),
        }


EXPECTED_SECTIONS = {
    "pt-BR": ("Resumo Profissional", "Habilidades Técnicas", "Experiência Profissional", "Formação"),
    "en-US": ("Professional Summary", "Technical Skills", "Professional Experience", "Education"),
}


def _fail(category: str, message: str) -> None:
    raise GroundingError(category, message)


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or "item"


def _nonempty(lines: Iterable[str]) -> list[str]:
    return [line for line in lines if line.strip()]


def _split_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _heading_entries(lines: list[str], prefix: str) -> list[list[str]]:
    entries: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            if current:
                entries.append(current)
            current = [line]
        elif current:
            if line.strip():
                current.append(line)
        elif line.strip():
            _fail("SOURCE_PARSE", "experience contains content before its first employer heading")
    if current:
        entries.append(current)
    return entries


def parse_source(text: str, language: str) -> SourceResume:
    """Parse one supported master Markdown file into exact source fragments."""

    if language not in EXPECTED_SECTIONS:
        _fail("SOURCE_PARSE", f"unsupported language: {language}")
    if not text.strip():
        _fail("SOURCE_PARSE", "source Markdown is empty")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or not re.fullmatch(r"#\s+.+", lines[0]):
        _fail("SOURCE_PARSE", "first line must be one identity heading")

    fragments: dict[str, Fragment] = {}
    order = 0

    def add(
        fragment_id: str,
        kind: str,
        section: str,
        value: str,
        owner: str | None = None,
        role: str | None = None,
        mandatory: bool = False,
        selectable: bool = False,
    ) -> str:
        nonlocal order
        if fragment_id in fragments:
            _fail("SOURCE_PARSE", f"fragment ID collision: {fragment_id}")
        if not value.strip():
            _fail("SOURCE_PARSE", f"empty fragment: {fragment_id}")
        if "```" in value or re.search(r"(?m)^\s*<[^>]+>", value):
            _fail("SOURCE_PARSE", f"unsupported Markdown structure in fragment: {fragment_id}")
        fragments[fragment_id] = Fragment(
            fragment_id,
            kind,
            section,
            owner,
            role,
            value,
            order,
            mandatory,
            selectable,
        )
        order += 1
        return fragment_id

    identity_id = add("identity.name", "identity", "preamble", lines[0], mandatory=True)
    preamble_lines = _nonempty(lines[1 : next((i for i, line in enumerate(lines[1:], 1) if line.startswith("## ")), len(lines))])
    if len(preamble_lines) != 3:
        _fail("SOURCE_PARSE", "preamble must contain headline, location, and contact")
    if "@" not in preamble_lines[2] or "linkedin.com/" not in preamble_lines[2] or "github.com/" not in preamble_lines[2]:
        _fail("SOURCE_PARSE", "contact line is missing expected public contact links")
    preamble_ids = [identity_id]
    preamble_ids.append(add("headline", "headline", "preamble", preamble_lines[0], mandatory=True))
    preamble_ids.append(add("contact.location", "location", "preamble", preamble_lines[1], mandatory=True))
    preamble_ids.append(add("contact.links", "contact", "preamble", preamble_lines[2], mandatory=True))

    headings = [(index, match.group(1)) for index, line in enumerate(lines) if (match := re.fullmatch(r"##\s+(.+?)\s*", line))]
    expected = EXPECTED_SECTIONS[language]
    if tuple(title for _, title in headings) != expected:
        _fail("SOURCE_PARSE", "required language-specific sections are missing or out of order")
    section_ids: dict[str, str] = {}
    section_ranges: dict[str, tuple[int, int]] = {}
    for position, (start, title) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        section_ids[title] = add(f"section.{_slug(title)}", "section_heading", title, lines[start], mandatory=True)
        section_ranges[title] = (start + 1, end)

    summary_title, skills_title, experience_title, education_title = expected
    summary_blocks = _split_blocks(lines[slice(*section_ranges[summary_title])])
    if not summary_blocks:
        _fail("SOURCE_PARSE", "professional summary is empty")
    summary_ids: list[str] = []
    for index, block in enumerate(summary_blocks, 1):
        if any(HEADING_RE.match(line) or BULLET_RE.match(line) or SKILL_RE.match(line) for line in block):
            _fail("SOURCE_PARSE", "summary contains unsupported Markdown structure")
        summary_ids.append(add(f"summary.{index}", "summary", summary_title, "\n".join(block), mandatory=False, selectable=True))

    skill_blocks = [[line] for line in lines[slice(*section_ranges[skills_title])] if line.strip()]
    if not skill_blocks:
        _fail("SOURCE_PARSE", "technical skills are empty")
    skill_ids: list[str] = []
    spoken_language_id: str | None = None
    for block in skill_blocks:
        if len(block) != 1:
            _fail("SOURCE_PARSE", "skill categories must be one-line blocks")
        match = SKILL_RE.fullmatch(block[0])
        if not match:
            _fail("SOURCE_PARSE", "skill section contains a malformed category line")
        label = match.group(1).strip()
        skill_id = add(
            f"skills.{_slug(label)}",
            "skill",
            skills_title,
            block[0],
            mandatory=False,
            selectable=True,
        )
        skill_ids.append(skill_id)
        if label.casefold() in {"idiomas", "spoken languages"}:
            if spoken_language_id is not None:
                _fail("SOURCE_PARSE", "spoken-language category appears more than once")
            spoken_language_id = skill_id
    if spoken_language_id is None:
        _fail("SOURCE_PARSE", "spoken-language category is required")

    experience_lines = lines[slice(*section_ranges[experience_title])]
    experience_blocks = _heading_entries(experience_lines, "### ")
    if not experience_blocks:
        _fail("SOURCE_PARSE", "professional experience is empty")
    employers: list[Employer] = []
    employer_keys: set[str] = set()
    for block in experience_blocks:
        if not block or not block[0].startswith("### "):
            _fail("SOURCE_PARSE", "each experience entry must start with a level-three heading")
        heading = block[0]
        heading_text = heading[4:].strip()
        fragment_ids: list[str] = []
        bullets: list[str] = []
        header_groups: list[tuple[str, ...]] = []
        if "|" in heading_text:
            role_text, employer_name = [part.strip() for part in heading_text.split("|", 1)]
            if not role_text or not employer_name:
                _fail("SOURCE_PARSE", "role and employer names are required")
            employer_key = _slug(employer_name)
            if employer_key in employer_keys:
                _fail("SOURCE_PARSE", f"ambiguous duplicate employer: {employer_name}")
            employer_keys.add(employer_key)
            role_id = add(
                f"experience.{employer_key}.role.{_slug(role_text)}",
                "role",
                experience_title,
                heading,
                owner=employer_name,
                role=role_text,
                mandatory=True,
            )
            fragment_ids.append(role_id)
            if len(block) < 2 or not DATE_RE.search(block[1]):
                _fail("SOURCE_PARSE", f"missing date for {employer_name}")
            date_id = add(
                f"experience.{employer_key}.date.1",
                "date",
                experience_title,
                block[1],
                owner=employer_name,
                role=role_text,
                mandatory=True,
            )
            fragment_ids.append(date_id)
            header_groups.append((role_id, date_id))
            bullet_lines = block[2:]
        else:
            employer_name = heading_text
            employer_key = _slug(employer_name)
            if not employer_name or employer_key in employer_keys:
                _fail("SOURCE_PARSE", f"ambiguous duplicate employer: {employer_name}")
            employer_keys.add(employer_key)
            employer_id = add(
                f"experience.{employer_key}.employer",
                "employer",
                experience_title,
                heading,
                owner=employer_name,
                mandatory=True,
            )
            fragment_ids.append(employer_id)
            if len(block) < 3 or block[1].startswith("-") or block[1].startswith("**"):
                _fail("SOURCE_PARSE", f"missing location for {employer_name}")
            location_id = add(
                f"experience.{employer_key}.location",
                "location",
                experience_title,
                block[1],
                owner=employer_name,
                mandatory=True,
            )
            fragment_ids.append(location_id)
            header_groups.append((employer_id, location_id))
            cursor = 2
            role_group: list[str] = []
            role_index = 0
            while cursor < len(block) and ROLE_DATE_RE.fullmatch(block[cursor]):
                role_index += 1
                role_match = ROLE_DATE_RE.fullmatch(block[cursor])
                assert role_match is not None
                role_text = role_match.group(1).strip()
                role_id = add(
                    f"experience.{employer_key}.role.{_slug(role_text)}",
                    "role_date",
                    experience_title,
                    block[cursor],
                    owner=employer_name,
                    role=role_text,
                    mandatory=True,
                )
                fragment_ids.append(role_id)
                role_group.append(role_id)
                cursor += 1
            if not role_group:
                _fail("SOURCE_PARSE", f"missing role and date for {employer_name}")
            header_groups.append(tuple(role_group))
            bullet_lines = block[cursor:]

        for bullet_index, bullet in enumerate(bullet_lines, 1):
            if not BULLET_RE.fullmatch(bullet):
                _fail("SOURCE_PARSE", f"malformed experience bullet for {employer_name}")
            bullet_id = add(
                f"experience.{employer_key}.bullet.{bullet_index}",
                "bullet",
                experience_title,
                bullet,
                owner=employer_name,
                mandatory=False,
                selectable=True,
            )
            fragment_ids.append(bullet_id)
            bullets.append(bullet_id)
        if not bullets:
            _fail("SOURCE_PARSE", f"employer has no experience bullets: {employer_name}")
        employers.append(Employer(employer_key, employer_name, tuple(fragment_ids), tuple(bullets), tuple(header_groups)))

    education_lines = lines[slice(*section_ranges[education_title])]
    education_blocks = _split_blocks(education_lines)
    if not education_blocks:
        _fail("SOURCE_PARSE", "education is empty")
    education: list[Education] = []
    education_keys: set[str] = set()
    for block in education_blocks:
        if len(block) < 2 or not block[0].startswith("### "):
            _fail("SOURCE_PARSE", "education entries must contain a level-three heading and date line")
        name = block[0][4:].strip()
        key = _slug(name)
        if key in education_keys:
            _fail("SOURCE_PARSE", f"ambiguous duplicate education entry: {name}")
        education_keys.add(key)
        if len(block) != 2 or not DATE_RE.search(block[1]):
            _fail("SOURCE_PARSE", f"malformed education entry: {name}")
        heading_id = add(f"education.{key}", "education", education_title, block[0], owner=name, mandatory=True)
        date_id = add(f"education.{key}.date", "education_date", education_title, block[1], owner=name, mandatory=True)
        education.append(Education(key, (heading_id, date_id)))

    if not employers:
        _fail("SOURCE_PARSE", "at least one employer is required")
    ordered_ids = list(preamble_ids)
    ordered_ids.append(section_ids[summary_title])
    ordered_ids.extend(summary_ids)
    ordered_ids.append(section_ids[skills_title])
    ordered_ids.extend(skill_ids)
    ordered_ids.append(section_ids[experience_title])
    for employer in employers:
        ordered_ids.extend(employer.fragment_ids)
    ordered_ids.append(section_ids[education_title])
    for entry in education:
        ordered_ids.extend(entry.fragment_ids)
    fragments = {
        fragment_id: replace(fragments[fragment_id], order=index)
        for index, fragment_id in enumerate(ordered_ids)
    }
    digest = hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()
    return SourceResume(
        language,
        text.replace("\r\n", "\n").replace("\r", "\n"),
        digest,
        fragments,
        section_ids,
        tuple(preamble_ids),
        tuple(summary_ids),
        tuple(skill_ids),
        spoken_language_id,
        tuple(employers),
        tuple(education),
    )


def _strict_json(raw: str) -> dict[str, Any]:
    candidate = raw.strip()
    if candidate.startswith("```"):
        fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", candidate, flags=re.IGNORECASE | re.DOTALL)
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


def normalize_vacancy_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        _fail("MODEL_SCHEMA", f"{field} must be a string")
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(char for char in normalized if unicodedata.category(char)[0] != "C" or char in "\t\n\r")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        _fail("MODEL_SCHEMA", f"{field} is empty after sanitization")
    if len(normalized) > limit:
        _fail("MODEL_SCHEMA", f"{field} exceeds {limit} characters")
    return normalized


def escape_markdown(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|>~-])", r"\\\1", value)


def _list_field(data: dict[str, Any], name: str) -> list[Any]:
    value = data.get(name)
    if not isinstance(value, list):
        _fail("MODEL_SCHEMA", f"{name} must be an array")
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


def _validate_response_v1(data: dict[str, Any], source: SourceResume) -> ValidatedSelection:
    """Validate the v1 closed model response and return a manifest-ready selection.

    Selectable evidence omitted from selected_fragment_ids is deterministically
    reconciled into the effective selection, which must then satisfy every
    selection limit. Mandatory structural evidence is already rendered and is
    never added to selected_fragment_ids.
    """

    _exact_keys(
        data,
        {
            "schema_version",
            "target_company",
            "target_role",
            "selected_fragment_ids",
            "vacancy_requirements",
            "requirement_classifications",
            "interview_topics",
        },
        "response",
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        _fail("MODEL_SCHEMA", f"unsupported schema version: {data['schema_version']!r}")
    company = normalize_vacancy_text(data["target_company"], "target_company", MAX_TARGET_TEXT)
    role = normalize_vacancy_text(data["target_role"], "target_role", MAX_TARGET_TEXT)

    selected_raw = _list_field(data, "selected_fragment_ids")
    selected: list[str] = []
    selected_set: set[str] = set()
    for value in selected_raw:
        if not isinstance(value, str) or not value:
            _fail("MODEL_SCHEMA", "selected fragment IDs must be non-empty strings")
        if value in selected_set:
            _fail("MODEL_SCHEMA", f"duplicate selected fragment ID: {value}")
        fragment = source.fragments.get(value)
        if fragment is None:
            _fail("MODEL_SCHEMA", f"unknown selected fragment ID: {value}")
        if not fragment.selectable:
            _fail("MODEL_SELECTION", f"fragment is not selectable: {value}")
        selected_set.add(value)
        selected.append(value)

    requirement_items = _list_field(data, "vacancy_requirements")
    if not requirement_items or len(requirement_items) > MAX_REQUIREMENTS:
        _fail("VACANCY_ANALYSIS", f"vacancy requirements must contain 1-{MAX_REQUIREMENTS} items")
    requirements: list[Requirement] = []
    requirement_ids: set[str] = set()
    for item in requirement_items:
        if not isinstance(item, dict):
            _fail("MODEL_SCHEMA", "vacancy requirements must be objects")
        _exact_keys(item, {"id", "text", "priority"}, "vacancy requirement")
        requirement_id = item["id"]
        if not isinstance(requirement_id, str) or not REQUIREMENT_ID_RE.fullmatch(requirement_id):
            _fail("MODEL_SCHEMA", f"invalid requirement ID: {requirement_id!r}")
        if requirement_id in requirement_ids:
            _fail("MODEL_SCHEMA", f"duplicate requirement ID: {requirement_id}")
        requirement_ids.add(requirement_id)
        priority = item["priority"]
        if priority not in {"required", "preferred", "context"}:
            _fail("MODEL_SCHEMA", f"invalid requirement priority: {priority!r}")
        requirements.append(Requirement(requirement_id, normalize_vacancy_text(item["text"], f"{requirement_id}.text", MAX_VACANCY_TEXT), priority))

    classification_items = _list_field(data, "requirement_classifications")
    if not classification_items or len(classification_items) > MAX_REQUIREMENTS:
        _fail("VACANCY_ANALYSIS", f"requirement classifications must contain 1-{MAX_REQUIREMENTS} items")
    strong_matches: list[Match] = []
    partial_matches: list[Match] = []
    gaps: list[str] = []
    classified: set[str] = set()
    reconciled: list[str] = []
    reconciled_set: set[str] = set()
    for item in classification_items:
        if not isinstance(item, dict):
            _fail("MODEL_SCHEMA", "requirement classifications must be objects")
        _exact_keys(item, {"requirement_id", "status", "evidence_ids"}, "requirement classification")
        requirement_id = item["requirement_id"]
        if requirement_id not in requirement_ids:
            _fail("MODEL_SCHEMA", f"classification references unknown requirement: {requirement_id}")
        if requirement_id in classified:
            _fail("MODEL_SCHEMA", f"duplicate requirement classification: {requirement_id}")
        classified.add(requirement_id)
        status = item["status"]
        if status not in {"strong", "partial", "gap"}:
            _fail("MODEL_SCHEMA", f"invalid requirement classification: {status!r}")
        evidence_values = _list_field(item, "evidence_ids")
        if status == "gap":
            if evidence_values:
                _fail("EVIDENCE_MAPPING", f"gap classification cannot contain evidence: {requirement_id}")
            gaps.append(requirement_id)
            continue
        if not evidence_values:
            _fail("EVIDENCE_MAPPING", f"{status} classification requires evidence: {requirement_id}")
        evidence_ids: list[str] = []
        for evidence_id in evidence_values:
            if not isinstance(evidence_id, str) or evidence_id in evidence_ids:
                _fail("EVIDENCE_MAPPING", f"invalid or duplicate evidence ID: {evidence_id!r}")
            fragment = source.fragments.get(evidence_id)
            if fragment is None:
                _fail("EVIDENCE_MAPPING", f"unknown evidence ID: {evidence_id}")
            if fragment.selectable and evidence_id not in selected_set and evidence_id not in reconciled_set:
                reconciled.append(evidence_id)
                reconciled_set.add(evidence_id)
            evidence_ids.append(evidence_id)
        match = Match(requirement_id, tuple(evidence_ids))
        if status == "strong":
            strong_matches.append(match)
        else:
            partial_matches.append(match)
    if classified != requirement_ids:
        missing = sorted(requirement_ids - classified)
        _fail("MODEL_SCHEMA", "every vacancy requirement must be classified exactly once (missing: " + ", ".join(missing) + ")")

    effective = selected + reconciled
    effective_set = set(effective)
    summaries = [item for item in effective if source.fragments[item].kind == "summary"]
    skills = [item for item in effective if source.fragments[item].kind == "skill"]
    bullets = [item for item in effective if source.fragments[item].kind == "bullet"]
    if not 1 <= len(effective) <= MAX_SELECTED_FRAGMENTS:
        _fail("MODEL_SELECTION", f"selected fragment count must be 1-{MAX_SELECTED_FRAGMENTS} after evidence reconciliation")
    if not 1 <= len(summaries) <= MAX_SUMMARY_FRAGMENTS:
        _fail("MODEL_SELECTION", "selection must contain one or two summary paragraphs")
    if len(skills) < 2 or source.spoken_language_id not in effective_set:
        _fail("MODEL_SELECTION", "selection must contain spoken languages and another skill category")
    if len(bullets) > MAX_TOTAL_BULLETS:
        _fail("MODEL_SELECTION", f"selection contains more than {MAX_TOTAL_BULLETS} bullets")
    for employer in source.employers:
        employer_bullets = [item for item in bullets if item in employer.bullet_ids]
        if not employer_bullets:
            _fail("MODEL_SELECTION", f"selection omits employer: {employer.name}")
        if len(employer_bullets) > MAX_BULLETS_PER_EMPLOYER:
            _fail("MODEL_SELECTION", f"selection contains more than {MAX_BULLETS_PER_EMPLOYER} bullets for {employer.name}")

    strong = tuple(strong_matches)
    partial = tuple(partial_matches)

    topic_items = _list_field(data, "interview_topics")
    topics: list[str] = []
    for item in topic_items:
        if not isinstance(item, dict):
            _fail("MODEL_SCHEMA", "interview topics must be objects")
        _exact_keys(item, {"requirement_id"}, "interview topic")
        requirement_id = item["requirement_id"]
        if requirement_id not in requirement_ids:
            _fail("MODEL_SCHEMA", f"interview topic references unknown requirement: {requirement_id}")
        if requirement_id in topics:
            _fail("MODEL_SCHEMA", f"duplicate interview topic: {requirement_id}")
        topics.append(requirement_id)

    return ValidatedSelection(
        SCHEMA_VERSION,
        source.language,
        source.digest,
        company,
        role,
        tuple(effective),
        tuple(requirements),
        strong,
        partial,
        tuple(gaps),
        tuple(topics),
    )


# ---------------------------------------------------------------------------
# v2: source-grounded generated candidate-facing content.
# ---------------------------------------------------------------------------

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
NUMERIC_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?[%+]?")
NUMERIC_FACT_RE = re.compile(
    r"(?<![0-9A-Za-zÀ-ÖØ-öø-ÿ])"
    r"(\d+(?:[.,]\d+)?)\+?"
    r"(?:\s*(%|[A-Za-zÀ-ÖØ-öø-ÿ]{1,20}))?"
    r"(?![0-9A-Za-zÀ-ÖØ-öø-ÿ])"
)
PERCENT_UNIT_ALIASES = frozenset({
    "%", "percent", "pct", "porcento", "porcentos", "porcentagem", "porcentagens",
})
GENERATED_FORBIDDEN_RE = re.compile(r"[#`<>*_]")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?:;|…—])\s+")
CERT_KEYWORDS = frozenset({
    "certificacao",
    "certificacoes",
    "certificado",
    "certificada",
    "certificados",
    "certificadas",
    "certification",
    "certifications",
    "certified",
    "certificate",
})
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


@dataclass(frozen=True)
class GeneratedBlock:
    block_id: str
    text: str
    source_fragment_ids: tuple[str, ...]
    requirement_ids: tuple[str, ...]


@dataclass(frozen=True)
class SkillGroup:
    source_fragment_id: str
    label: str
    items: tuple[str, ...]
    requirement_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExperienceEntry:
    employer: str
    employer_key: str
    bullets: tuple[GeneratedBlock, ...]


@dataclass(frozen=True)
class AdvisoryWarning:
    code: str
    block_id: str
    message: str


@dataclass(frozen=True)
class ValidatedGeneration:
    schema_version: int
    language: str
    source_digest: str
    target_company: str
    target_role: str
    headline: GeneratedBlock | None
    summaries: tuple[GeneratedBlock, ...]
    skill_groups: tuple[SkillGroup, ...]
    experience: tuple[ExperienceEntry, ...]
    rendered_fragment_ids: tuple[str, ...]
    classification_evidence_ids: tuple[str, ...]
    requirements: tuple[Requirement, ...]
    strong_matches: tuple[Match, ...]
    partial_matches: tuple[Match, ...]
    gaps: tuple[str, ...]
    interview_topics: tuple[str, ...]
    warnings: tuple[AdvisoryWarning, ...]

    @property
    def selected_fragment_ids(self) -> tuple[str, ...]:
        return self.rendered_fragment_ids

    def all_bullets(self) -> tuple[GeneratedBlock, ...]:
        return tuple(bullet for entry in self.experience for bullet in entry.bullets)

    def to_manifest(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION_V2,
            "language": self.language,
            "source_digest": self.source_digest,
            "target_company": self.target_company,
            "target_role": self.target_role,
            "summary": [_block_manifest(item) for item in self.summaries],
            "skill_groups": [
                {
                    "source_fragment_id": item.source_fragment_id,
                    "label": item.label,
                    "items": list(item.items),
                    "requirement_ids": list(item.requirement_ids),
                }
                for item in self.skill_groups
            ],
            "experience": [
                {
                    "employer": item.employer,
                    "employer_key": item.employer_key,
                    "bullets": [_block_manifest(bullet) for bullet in item.bullets],
                }
                for item in self.experience
            ],
            "rendered_fragment_ids": list(self.rendered_fragment_ids),
            "classification_evidence_ids": list(self.classification_evidence_ids),
            "vacancy_requirements": [
                {"id": item.id, "text": item.text, "priority": item.priority}
                for item in self.requirements
            ],
            "classifications": _classifications_manifest(self.strong_matches, self.partial_matches, self.gaps),
            "interview_topics": list(self.interview_topics),
            "warnings": [
                {"code": item.code, "block_id": item.block_id, "message": item.message}
                for item in self.warnings
            ],
            "review_required": True,
        }
        if self.headline is not None:
            data["headline"] = _block_manifest(self.headline)
        return data


@dataclass(frozen=True)
class VocabularyTerm:
    tokens: tuple[str, ...]
    kind: str
    label: str


@dataclass(frozen=True)
class SourceVocabulary:
    terms: tuple[VocabularyTerm, ...]
    single_tokens: frozenset[str]
    source_tokens: tuple[str, ...]


def _block_manifest(block: GeneratedBlock) -> dict[str, Any]:
    return {
        "text": block.text,
        "source_fragment_ids": list(block.source_fragment_ids),
        "requirement_ids": list(block.requirement_ids),
    }


def _classifications_manifest(
    strong: tuple[Match, ...],
    partial: tuple[Match, ...],
    gaps: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    classifications: dict[str, dict[str, Any]] = {}
    for match in strong:
        classifications[match.requirement_id] = {"status": "strong", "evidence_ids": list(match.evidence_ids)}
    for match in partial:
        classifications[match.requirement_id] = {"status": "partial", "evidence_ids": list(match.evidence_ids)}
    for requirement_id in gaps:
        classifications[requirement_id] = {"status": "gap", "evidence_ids": []}
    return classifications


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


def _whitespace_key(value: str) -> str:
    """Regime B key: invisible removal and whitespace collapse only.

    No NFKC compatibility folding, case folding, accent folding, or plural
    normalization, so skill items must echo the source exactly.
    """

    normalized = _strip_invisibles(value)
    normalized = "".join(char for char in normalized if unicodedata.category(char)[0] != "C" or char in "\t\n\r")
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_numeric_text(text: str) -> str:
    normalized = _strip_invisibles(unicodedata.normalize("NFKC", text))
    return "".join(char for char in normalized if unicodedata.category(char)[0] != "C" or char in "\t\n\r")


def _numeric_unit(token: str) -> str:
    """Normalize a unit or unit-like context token.

    Percentage expressions are folded to the canonical ``%`` unit. Every other
    alphabetic token after a number is preserved as unit-like context instead
    of being silently discarded, so ``2 defects`` cannot be supported by a
    cited ``2 years``.
    """

    candidate = _normalize_token(token)
    if _token_variants(candidate) & PERCENT_UNIT_ALIASES:
        return "%"
    return candidate


def _numeric_facts(text: str) -> tuple[tuple[str, str | None], ...]:
    """Extract boundary-delimited numeric facts as (value, unit) pairs.

    Digits embedded in alphanumeric tokens such as ``E2E`` are not numbers, so
    they can never support an unrelated claim like ``2 years``. A numeric fact
    only matches an identical value; when the generated text attaches a unit or
    unit-like context, the cited evidence must attach the same
    (plural-normalized) unit. A bare generated number matches the same cited
    value with any unit, and ``+`` is a modifier rather than a unit.
    """

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


def _numeric_fact_supported(
    value: str,
    unit: str | None,
    cited_facts: tuple[tuple[str, str | None], ...],
) -> bool:
    candidates = [cited_unit for cited_value, cited_unit in cited_facts if cited_value == value]
    if not candidates:
        return False
    if unit is None:
        return True
    variants = _token_variants(unit)
    return any(
        cited_unit is not None and variants & _token_variants(cited_unit)
        for cited_unit in candidates
    )


def _token_variants(token: str) -> frozenset[str]:
    variants = {token}
    if len(token) >= 5 and token.endswith("ies"):
        variants.add(token[:-3] + "y")
    if len(token) >= 6 and token.endswith("es") and not token.endswith("ses"):
        variants.add(token[:-2])
    if len(token) >= 5 and token.endswith("s") and not token.endswith("ss"):
        variants.add(token[:-1])
    return frozenset(variants)


def _covers(term_tokens: tuple[str, ...], corpus_tokens: tuple[str, ...]) -> bool:
    length = len(term_tokens)
    if length == 0 or length > len(corpus_tokens):
        return False
    variants = [_token_variants(token) for token in term_tokens]
    for start in range(len(corpus_tokens) - length + 1):
        if all(variants[index] & _token_variants(corpus_tokens[start + index]) for index in range(length)):
            return True
    return False


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


def build_source_vocabulary(source: SourceResume) -> SourceVocabulary:
    seen: set[tuple[str, tuple[str, ...]]] = set()
    terms: list[VocabularyTerm] = []

    def add(kind: str, label: str) -> None:
        tokens = _normalize_tokens(label)
        if not tokens:
            return
        key = (kind, tokens)
        if key in seen:
            return
        seen.add(key)
        terms.append(VocabularyTerm(tokens, kind, label))

    for employer in source.employers:
        add("employer", employer.name)
    for fragment in source.fragments.values():
        if fragment.kind == "skill":
            match = SKILL_RE.fullmatch(fragment.text)
            if match is None:
                continue
            add("skill_label", match.group(1).strip())
            for item in match.group(2).split(","):
                item = item.strip()
                if item:
                    add("skill_item", item)
        elif fragment.kind in {"role", "role_date"} and fragment.role:
            add("title", fragment.role)
    for entry in source.education:
        owner = source.fragments[entry.fragment_ids[0]].owner
        if owner:
            add("education", owner)
    single = frozenset(term.tokens[0] for term in terms if len(term.tokens) == 1)
    return SourceVocabulary(tuple(terms), single, _normalize_tokens(source.text))


def _check_generated_text(
    source: SourceResume,
    vocabulary: SourceVocabulary,
    gap_phrases: tuple[tuple[str, ...], ...],
    gap_atoms: frozenset[str],
    block_id: str,
    text: str,
    cited_fragments: tuple[Fragment, ...],
) -> tuple[AdvisoryWarning, ...]:
    corpus_tokens = tuple(
        token for fragment in cited_fragments for token in _normalize_tokens(fragment.text)
    )
    text_tokens = _normalize_tokens(text)
    raw_tokens = _raw_tokens(text)
    token_set = set(text_tokens)
    warnings: list[AdvisoryWarning] = []

    for phrase in gap_phrases:
        if not _covers(phrase, text_tokens):
            continue
        rendered = " ".join(phrase)
        if _covers(phrase, corpus_tokens):
            _fail(
                "CLASSIFICATION_CONFLICT",
                f"{block_id} claims gap requirement '{rendered}' with supporting evidence; "
                "reclassify the requirement or remove the claim",
            )
        _fail(
            "UNSUPPORTED_REQUIREMENT",
            f"{block_id} claims gap requirement '{rendered}' without supporting evidence",
        )
    for atom in sorted(gap_atoms):
        if atom not in token_set:
            continue
        if _covers((atom,), corpus_tokens):
            _fail(
                "CLASSIFICATION_CONFLICT",
                f"{block_id} uses gap requirement term '{atom}' with supporting evidence; "
                "reclassify the requirement or remove the claim",
            )
        _fail(
            "UNSUPPORTED_REQUIREMENT",
            f"{block_id} uses unsupported gap term '{atom}'",
        )

    other_language = "en-US" if source.language == "pt-BR" else "pt-BR"
    for heading in EXPECTED_SECTIONS[other_language]:
        if _covers(_normalize_tokens(heading), text_tokens):
            _fail("GENERATED_TEXT", f"{block_id} contains a section heading from the wrong output language")

    for term in vocabulary.terms:
        if not _covers(term.tokens, text_tokens):
            continue
        if not _covers(term.tokens, corpus_tokens):
            _fail(
                "UNSUPPORTED_PROVENANCE",
                f"{block_id} uses source {term.kind} '{term.label}' without citing supporting evidence",
            )
        if term.kind == "employer" and not any(fragment.owner == term.label for fragment in cited_fragments):
            _fail(
                "EVIDENCE_PROVENANCE",
                f"{block_id} mentions employer '{term.label}' without citing an entry owned by that employer",
            )

    cited_facts = _numeric_facts(" ".join(fragment.text for fragment in cited_fragments))
    for value, unit in _numeric_facts(text):
        if _numeric_fact_supported(value, unit, cited_facts):
            continue
        claim = value if unit is None else f"{value} {unit}"
        _fail("UNSUPPORTED_CLAIM", f"{block_id} contains unsupported metric or date '{claim}'")

    for raw, _sentence_start in raw_tokens:
        if NUMERIC_TOKEN_RE.fullmatch(raw) or not _is_pattern_token(raw):
            continue
        token = _normalize_token(raw)
        if _covers((token,), corpus_tokens):
            continue
        if _covers((token,), vocabulary.source_tokens):
            _fail("UNSUPPORTED_PROVENANCE", f"{block_id} uses '{raw}' without citing supporting evidence")
        _fail("UNSUPPORTED_CLAIM", f"{block_id} contains unsupported term '{raw}'")

    sentences: list[list[str]] = []
    current: list[str] = []
    for raw, sentence_start in raw_tokens:
        if sentence_start and current:
            sentences.append(current)
            current = []
        current.append(raw)
    if current:
        sentences.append(current)
    for sentence in sentences:
        for index, raw in enumerate(sentence):
            if _normalize_token(raw) not in CERT_KEYWORDS:
                continue
            for candidate in sentence[index + 1 : index + 5]:
                if len(candidate) < 2:
                    continue
                if not (_is_pattern_token(candidate) or (candidate[:1].isupper() and candidate[1:].islower())):
                    continue
                if _covers((_normalize_token(candidate),), corpus_tokens):
                    continue
                _fail(
                    "UNSUPPORTED_CLAIM",
                    f"{block_id} contains unsupported certification claim '{candidate}'",
                )

    for raw, sentence_start in raw_tokens:
        if sentence_start or _is_pattern_token(raw) or not raw[:1].isupper():
            continue
        token = _normalize_token(raw)
        if token in vocabulary.single_tokens or token in STOPWORDS:
            continue
        if _covers((token,), corpus_tokens) or _covers((token,), vocabulary.source_tokens):
            continue
        _fail("UNSUPPORTED_CLAIM", f"{block_id} contains unsupported proper noun or technology '{raw}'")

    unsupported_content: list[str] = []
    paraphrase: list[str] = []
    supported_count = 0
    content_count = 0
    for token in dict.fromkeys(text_tokens):
        if len(token) < 3 or token in STOPWORDS:
            continue
        content_count += 1
        if _covers((token,), corpus_tokens):
            supported_count += 1
            continue
        if _covers((token,), vocabulary.source_tokens):
            unsupported_content.append(token)
        else:
            paraphrase.append(token)
    if unsupported_content:
        warnings.append(AdvisoryWarning(
            "UNSUPPORTED_CONTENT_WORD",
            block_id,
            "words used elsewhere in the master resume but not in this block's evidence: "
            + ", ".join(unsupported_content[:8]),
        ))
    if paraphrase:
        warnings.append(AdvisoryWarning(
            "PARAPHRASE_REVIEW",
            block_id,
            "new ordinary wording not found in this block's evidence: " + ", ".join(paraphrase[:8]),
        ))
    if content_count and supported_count == 0:
        warnings.append(AdvisoryWarning(
            "UNMOORED_TEXT",
            block_id,
            "generated text has no content-word overlap with its cited evidence",
        ))

    unknown_capitalized: list[str] = []
    for raw, sentence_start in raw_tokens:
        if not sentence_start or _is_pattern_token(raw) or not raw[:1].isupper():
            continue
        token = _normalize_token(raw)
        if token in vocabulary.single_tokens or token in STOPWORDS:
            continue
        if _covers((token,), vocabulary.source_tokens):
            continue
        unknown_capitalized.append(raw)
    if unknown_capitalized:
        warnings.append(AdvisoryWarning(
            "POSSIBLE_UNSUPPORTED_PROPER_NOUN",
            block_id,
            "capitalized words not found in the master resume: "
            + ", ".join(list(dict.fromkeys(unknown_capitalized))[:8]),
        ))

    pt_hits = sum(1 for token in text_tokens if token in PT_STOPWORDS)
    en_hits = sum(1 for token in text_tokens if token in EN_STOPWORDS)
    if source.language == "pt-BR" and en_hits >= 3 and en_hits > pt_hits:
        warnings.append(AdvisoryWarning(
            "LANGUAGE_MISMATCH",
            block_id,
            "generated text appears to be in a different language than pt-BR",
        ))
    elif source.language == "en-US" and pt_hits >= 3 and pt_hits > en_hits:
        warnings.append(AdvisoryWarning(
            "LANGUAGE_MISMATCH",
            block_id,
            "generated text appears to be in a different language than en-US",
        ))

    return tuple(warnings)


def _near_duplicate(first: GeneratedBlock, second: GeneratedBlock) -> bool:
    left = frozenset(
        token for token in _normalize_tokens(first.text) if len(token) >= 3 and token not in STOPWORDS
    )
    right = frozenset(
        token for token in _normalize_tokens(second.text) if len(token) >= 3 and token not in STOPWORDS
    )
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= NEAR_DUPLICATE_RATIO


def _v2_string_list(
    value: Any,
    field: str,
    *,
    min_items: int = 0,
    max_items: int | None = None,
) -> list[str]:
    if not isinstance(value, list):
        _fail("MODEL_SCHEMA", f"{field} must be an array")
    if len(value) < min_items:
        _fail("MODEL_SCHEMA", f"{field} must contain at least {min_items} items")
    if max_items is not None and len(value) > max_items:
        _fail("MODEL_SCHEMA", f"{field} must contain at most {max_items} items")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            _fail("MODEL_SCHEMA", f"{field} entries must be non-empty strings")
        items.append(item)
    if len(set(items)) != len(items):
        _fail("MODEL_SCHEMA", f"{field} contains duplicate values")
    return items


def _v2_text(value: Any, field: str, min_chars: int, max_chars: int) -> str:
    if not isinstance(value, str):
        _fail("MODEL_SCHEMA", f"{field} must be a string")
    normalized = _strip_invisibles(unicodedata.normalize("NFKC", value))
    normalized = "".join(
        char for char in normalized
        if unicodedata.category(char)[0] != "C" or char in "\t\n\r"
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if len(normalized) < min_chars:
        _fail("GENERATED_TEXT", f"{field} is shorter than {min_chars} characters")
    if len(normalized) > max_chars:
        _fail("GENERATED_TEXT", f"{field} exceeds {max_chars} characters")
    if GENERATED_FORBIDDEN_RE.search(normalized):
        _fail("GENERATED_TEXT", f"{field} contains unsupported Markdown or control characters")
    return normalized


def _v2_requirement_links(value: Any, field: str, requirement_ids: set[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    ids = _v2_string_list(value, field, max_items=MAX_REQUIREMENT_LINKS_PER_BLOCK)
    for item in ids:
        if item not in requirement_ids:
            _fail("MODEL_SCHEMA", f"{field} references unknown requirement: {item}")
    return tuple(ids)


def _v2_fragments(ids: list[str], source: SourceResume) -> tuple[Fragment, ...]:
    fragments: list[Fragment] = []
    for fragment_id in ids:
        fragment = source.fragments.get(fragment_id)
        if fragment is None:
            _fail("EVIDENCE_PROVENANCE", f"unknown source fragment ID: {fragment_id}")
        fragments.append(fragment)
    return tuple(fragments)


def _v2_block_keys(item: dict[str, Any], field: str) -> None:
    allowed = {"text", "source_fragment_ids", "requirement_ids"}
    unknown = sorted(set(item) - allowed)
    missing = sorted({"text", "source_fragment_ids"} - set(item))
    if unknown or missing:
        detail: list[str] = []
        if unknown:
            detail.append("unknown: " + ", ".join(unknown))
        if missing:
            detail.append("missing: " + ", ".join(missing))
        _fail("MODEL_SCHEMA", f"{field} fields are invalid ({'; '.join(detail)})")


def _v2_parse_block(
    item: Any,
    field: str,
    block_id: str,
    source: SourceResume,
    requirement_ids: set[str],
    min_chars: int,
    max_chars: int,
) -> tuple[GeneratedBlock, tuple[Fragment, ...]]:
    if not isinstance(item, dict):
        _fail("MODEL_SCHEMA", f"{field} must be an object")
    _v2_block_keys(item, field)
    text = _v2_text(item["text"], f"{field}.text", min_chars, max_chars)
    ids = _v2_string_list(
        item["source_fragment_ids"],
        f"{field}.source_fragment_ids",
        min_items=1,
        max_items=MAX_PROVENANCE_PER_BLOCK,
    )
    links = _v2_requirement_links(item.get("requirement_ids"), f"{field}.requirement_ids", requirement_ids)
    fragments = _v2_fragments(ids, source)
    return GeneratedBlock(block_id, text, tuple(ids), links), fragments


def _v2_skill_items(value: Any, field: str, fragment: Fragment) -> tuple[str, ...]:
    match = SKILL_RE.fullmatch(fragment.text)
    assert match is not None
    canonical = {
        _whitespace_key(item): item.strip()
        for item in match.group(2).split(",")
        if item.strip()
    }
    raw_items = _v2_string_list(value, f"{field}.items", min_items=1, max_items=MAX_SKILL_ITEMS)
    items: list[str] = []
    keys: set[str] = set()
    for item in raw_items:
        if len(item) > MAX_SKILL_ITEM_CHARS:
            _fail("GENERATED_TEXT", f"{field}.items entry exceeds {MAX_SKILL_ITEM_CHARS} characters")
        key = _whitespace_key(item)
        if key in keys:
            _fail("MODEL_SCHEMA", f"{field}.items contains duplicate skill items")
        keys.add(key)
        source_item = canonical.get(key)
        if source_item is None:
            _fail(
                "EVIDENCE_PROVENANCE",
                f"{field} item is not an exact source item of {fragment.id}: {item}",
            )
        items.append(source_item)
    return tuple(items)


def validate_generated_response(data: dict[str, Any], source: SourceResume) -> ValidatedGeneration:
    """Validate the v2 generated-content contract and return a manifest-ready generation."""

    core = {
        "schema_version",
        "target_company",
        "target_role",
        "vacancy_requirements",
        "requirement_classifications",
        "summary",
        "skill_groups",
        "experience",
        "interview_topics",
    }
    allowed = core | {"headline"}
    unknown = sorted(set(data) - allowed)
    missing = sorted(core - set(data))
    if unknown or missing:
        detail: list[str] = []
        if unknown:
            detail.append("unknown: " + ", ".join(unknown))
        if missing:
            detail.append("missing: " + ", ".join(missing))
        _fail("MODEL_SCHEMA", f"response fields are invalid ({'; '.join(detail)})")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION_V2:
        _fail("MODEL_SCHEMA", f"unsupported schema version: {data['schema_version']!r}")
    company = normalize_vacancy_text(data["target_company"], "target_company", MAX_TARGET_TEXT)
    role = normalize_vacancy_text(data["target_role"], "target_role", MAX_TARGET_TEXT)

    requirement_items = _list_field(data, "vacancy_requirements")
    if not requirement_items or len(requirement_items) > MAX_REQUIREMENTS:
        _fail("VACANCY_ANALYSIS", f"vacancy requirements must contain 1-{MAX_REQUIREMENTS} items")
    requirements: list[Requirement] = []
    requirement_ids: set[str] = set()
    for item in requirement_items:
        if not isinstance(item, dict):
            _fail("MODEL_SCHEMA", "vacancy requirements must be objects")
        _exact_keys(item, {"id", "text", "priority"}, "vacancy requirement")
        requirement_id = item["id"]
        if not isinstance(requirement_id, str) or not REQUIREMENT_ID_RE.fullmatch(requirement_id):
            _fail("MODEL_SCHEMA", f"invalid requirement ID: {requirement_id!r}")
        if requirement_id in requirement_ids:
            _fail("MODEL_SCHEMA", f"duplicate requirement ID: {requirement_id}")
        requirement_ids.add(requirement_id)
        priority = item["priority"]
        if priority not in {"required", "preferred", "context"}:
            _fail("MODEL_SCHEMA", f"invalid requirement priority: {priority!r}")
        requirements.append(Requirement(
            requirement_id,
            normalize_vacancy_text(item["text"], f"{requirement_id}.text", MAX_VACANCY_TEXT),
            priority,
        ))

    classification_items = _list_field(data, "requirement_classifications")
    if not classification_items or len(classification_items) > MAX_REQUIREMENTS:
        _fail("VACANCY_ANALYSIS", f"requirement classifications must contain 1-{MAX_REQUIREMENTS} items")
    strong_matches: list[Match] = []
    partial_matches: list[Match] = []
    gaps: list[str] = []
    classified: set[str] = set()
    evidence_union: list[str] = []
    evidence_total = 0
    for item in classification_items:
        if not isinstance(item, dict):
            _fail("MODEL_SCHEMA", "requirement classifications must be objects")
        _exact_keys(item, {"requirement_id", "status", "evidence_ids"}, "requirement classification")
        requirement_id = item["requirement_id"]
        if requirement_id not in requirement_ids:
            _fail("MODEL_SCHEMA", f"classification references unknown requirement: {requirement_id}")
        if requirement_id in classified:
            _fail("MODEL_SCHEMA", f"duplicate requirement classification: {requirement_id}")
        classified.add(requirement_id)
        status = item["status"]
        if status not in {"strong", "partial", "gap"}:
            _fail("MODEL_SCHEMA", f"invalid requirement classification: {status!r}")
        evidence_values = _v2_string_list(
            item["evidence_ids"],
            f"{requirement_id}.evidence_ids",
            max_items=MAX_CLASSIFICATION_EVIDENCE,
        )
        if status == "gap":
            if evidence_values:
                _fail("EVIDENCE_MAPPING", f"gap classification cannot contain evidence: {requirement_id}")
            gaps.append(requirement_id)
            continue
        if not evidence_values:
            _fail("EVIDENCE_MAPPING", f"{status} classification requires evidence: {requirement_id}")
        for evidence_id in evidence_values:
            if evidence_id not in source.fragments:
                _fail("EVIDENCE_MAPPING", f"unknown evidence ID: {evidence_id}")
            if evidence_id not in evidence_union:
                evidence_union.append(evidence_id)
        evidence_total += len(evidence_values)
        match = Match(requirement_id, tuple(evidence_values))
        if status == "strong":
            strong_matches.append(match)
        else:
            partial_matches.append(match)
    if classified != requirement_ids:
        missing_requirements = sorted(requirement_ids - classified)
        _fail(
            "MODEL_SCHEMA",
            "every vacancy requirement must be classified exactly once (missing: "
            + ", ".join(missing_requirements) + ")",
        )
    if evidence_total > MAX_CLASSIFICATION_EVIDENCE_TOTAL:
        _fail(
            "EVIDENCE_MAPPING",
            f"classification evidence exceeds {MAX_CLASSIFICATION_EVIDENCE_TOTAL} entries",
        )
    gap_set = set(gaps)

    vocabulary = build_source_vocabulary(source)
    gap_phrases: list[tuple[str, ...]] = []
    gap_atoms: set[str] = set()
    for requirement in requirements:
        if requirement.id not in gap_set:
            continue
        tokens = _normalize_tokens(requirement.text)
        if tokens:
            gap_phrases.append(tokens)
        for token in tokens:
            if (
                len(token) >= 3
                and token not in STOPWORDS
                and not _covers((token,), vocabulary.source_tokens)
            ):
                gap_atoms.add(token)
    gap_phrase_set = tuple(gap_phrases)
    gap_atom_set = frozenset(gap_atoms)

    warnings: list[AdvisoryWarning] = []
    rendered_ids: list[str] = []
    assessed_blocks: list[tuple[GeneratedBlock, tuple[Fragment, ...]]] = []

    headline: GeneratedBlock | None = None
    if "headline" in data:
        headline, headline_fragments = _v2_parse_block(
            data["headline"],
            "headline",
            "headline",
            source,
            requirement_ids,
            MIN_HEADLINE_CHARS,
            MAX_HEADLINE_CHARS,
        )
        if "headline" not in headline.source_fragment_ids:
            _fail("EVIDENCE_PROVENANCE", "headline must cite the master resume headline fragment")
        assessed_blocks.append((headline, headline_fragments))
        for fragment_id in headline.source_fragment_ids:
            if fragment_id not in rendered_ids:
                rendered_ids.append(fragment_id)

    summary_values = _list_field(data, "summary")
    if not 1 <= len(summary_values) <= MAX_SUMMARY_BLOCKS:
        _fail("MODEL_SCHEMA", f"summary must contain 1-{MAX_SUMMARY_BLOCKS} blocks")
    summaries: list[GeneratedBlock] = []
    for index, item in enumerate(summary_values, 1):
        block, fragments = _v2_parse_block(
            item,
            f"summary.{index}",
            f"summary.{index}",
            source,
            requirement_ids,
            MIN_SUMMARY_CHARS,
            MAX_SUMMARY_CHARS,
        )
        if not any(fragment.kind == "summary" for fragment in fragments):
            _fail("EVIDENCE_PROVENANCE", f"summary.{index} must cite at least one master summary fragment")
        summaries.append(block)
        assessed_blocks.append((block, fragments))
        for fragment_id in block.source_fragment_ids:
            if fragment_id not in rendered_ids:
                rendered_ids.append(fragment_id)

    raw_groups = _list_field(data, "skill_groups")
    effective_group_max = len(source.skill_ids)
    if not 2 <= len(raw_groups) <= effective_group_max:
        _fail("MODEL_SCHEMA", f"skill groups must contain 2-{effective_group_max} items")
    skill_groups: list[SkillGroup] = []
    group_fragment_ids: set[str] = set()
    for index, item in enumerate(raw_groups, 1):
        field = f"skill_groups.{index}"
        if not isinstance(item, dict):
            _fail("MODEL_SCHEMA", "skill groups must be objects")
        unknown_keys = sorted(set(item) - {"source_fragment_id", "items", "requirement_ids"})
        missing_keys = sorted({"source_fragment_id", "items"} - set(item))
        if unknown_keys or missing_keys:
            detail = []
            if unknown_keys:
                detail.append("unknown: " + ", ".join(unknown_keys))
            if missing_keys:
                detail.append("missing: " + ", ".join(missing_keys))
            _fail("MODEL_SCHEMA", f"{field} fields are invalid ({'; '.join(detail)})")
        fragment_id = item["source_fragment_id"]
        if not isinstance(fragment_id, str) or not fragment_id:
            _fail("MODEL_SCHEMA", f"{field}.source_fragment_id must be a non-empty string")
        fragment = source.fragments.get(fragment_id)
        if fragment is None:
            _fail("EVIDENCE_PROVENANCE", f"unknown source fragment ID: {fragment_id}")
        if fragment.kind != "skill":
            _fail("EVIDENCE_PROVENANCE", f"skill group source fragment is not a skill category: {fragment_id}")
        if fragment_id in group_fragment_ids:
            _fail("MODEL_SCHEMA", f"duplicate skill group: {fragment_id}")
        group_fragment_ids.add(fragment_id)
        match = SKILL_RE.fullmatch(fragment.text)
        if match is None:
            _fail("EVIDENCE_PROVENANCE", f"skill fragment is malformed: {fragment_id}")
        label = match.group(1).strip()
        items = _v2_skill_items(item["items"], field, fragment)
        links = _v2_requirement_links(item.get("requirement_ids"), f"{field}.requirement_ids", requirement_ids)
        if len(f"{label}: {', '.join(items)}") > MAX_SKILL_GROUP_CHARS:
            _fail("GENERATED_TEXT", f"{field} exceeds {MAX_SKILL_GROUP_CHARS} rendered characters")
        skill_groups.append(SkillGroup(fragment_id, label, items, links))
        if fragment_id not in rendered_ids:
            rendered_ids.append(fragment_id)
    if source.spoken_language_id not in group_fragment_ids:
        _fail("MODEL_SELECTION", "skill groups must include the spoken-language category")

    raw_experience = _list_field(data, "experience")
    if not raw_experience or len(raw_experience) > len(source.employers):
        _fail("MODEL_SCHEMA", "experience must contain one entry per source employer")
    employers_by_name = {employer.name: employer for employer in source.employers}
    seen_employers: set[str] = set()
    experience: list[ExperienceEntry] = []
    bullet_blocks: list[GeneratedBlock] = []
    for index, item in enumerate(raw_experience, 1):
        field = f"experience.{index}"
        if not isinstance(item, dict):
            _fail("MODEL_SCHEMA", "experience entries must be objects")
        unknown_keys = sorted(set(item) - {"employer", "bullets"})
        missing_keys = sorted({"employer", "bullets"} - set(item))
        if unknown_keys or missing_keys:
            _fail("MODEL_SCHEMA", f"{field} fields are invalid")
        employer_name = item["employer"]
        employer = employers_by_name.get(employer_name) if isinstance(employer_name, str) else None
        if employer is None:
            _fail("STRUCTURE", f"experience employer is not an exact master employer name: {employer_name!r}")
        if employer.name in seen_employers:
            _fail("STRUCTURE", f"duplicate experience entry: {employer.name}")
        seen_employers.add(employer.name)
        bullet_items = _list_field(item, "bullets")
        if not 1 <= len(bullet_items) <= MAX_BULLETS_PER_EMPLOYER:
            _fail("MODEL_SELECTION", f"{field} must contain 1-{MAX_BULLETS_PER_EMPLOYER} bullets")
        bullets: list[GeneratedBlock] = []
        for bullet_index, bullet_item in enumerate(bullet_items, 1):
            block_id = f"experience.{employer.key}.bullet.{bullet_index}"
            bullet, fragments = _v2_parse_block(
                bullet_item,
                block_id,
                block_id,
                source,
                requirement_ids,
                MIN_BULLET_CHARS,
                MAX_BULLET_CHARS,
            )
            for fragment in fragments:
                if fragment.kind == "bullet":
                    if fragment.owner != employer.name:
                        _fail(
                            "EVIDENCE_PROVENANCE",
                            f"{block_id} cites a bullet owned by {fragment.owner}, not {employer.name}",
                        )
                elif fragment.kind != "skill":
                    _fail(
                        "EVIDENCE_PROVENANCE",
                        f"{block_id} may cite only same-employer bullets or skill fragments: {fragment.id}",
                    )
            if not any(fragment.kind == "bullet" for fragment in fragments):
                _fail(
                    "EVIDENCE_PROVENANCE",
                    f"{block_id} must cite at least one source bullet for {employer.name}",
                )
            if len(bullet.text) > LENGTH_REVIEW_BULLET_CHARS:
                warnings.append(AdvisoryWarning(
                    "LENGTH_REVIEW",
                    block_id,
                    f"bullet is longer than {LENGTH_REVIEW_BULLET_CHARS} characters",
                ))
            bullets.append(bullet)
            bullet_blocks.append(bullet)
            assessed_blocks.append((bullet, fragments))
            for fragment_id in bullet.source_fragment_ids:
                if fragment_id not in rendered_ids:
                    rendered_ids.append(fragment_id)
        experience.append(ExperienceEntry(employer.name, employer.key, tuple(bullets)))
    if seen_employers != set(employers_by_name):
        missing_employers = sorted(set(employers_by_name) - seen_employers)
        _fail("STRUCTURE", "experience omits employers: " + ", ".join(missing_employers))
    total_bullets = len(bullet_blocks)
    if total_bullets > MAX_TOTAL_BULLETS:
        _fail("MODEL_SELECTION", f"experience contains more than {MAX_TOTAL_BULLETS} bullets")
    seen_bullet_texts: dict[str, str] = {}
    for bullet in bullet_blocks:
        text_key = " ".join(_normalize_tokens(bullet.text))
        if text_key in seen_bullet_texts:
            _fail(
                "STRUCTURE",
                f"duplicate experience bullet text: {bullet.block_id} duplicates {seen_bullet_texts[text_key]}",
            )
        seen_bullet_texts[text_key] = bullet.block_id

    topic_items = _list_field(data, "interview_topics")
    topics: list[str] = []
    for item in topic_items:
        if not isinstance(item, dict):
            _fail("MODEL_SCHEMA", "interview topics must be objects")
        _exact_keys(item, {"requirement_id"}, "interview topic")
        requirement_id = item["requirement_id"]
        if requirement_id not in requirement_ids:
            _fail("MODEL_SCHEMA", f"interview topic references unknown requirement: {requirement_id}")
        if requirement_id in topics:
            _fail("MODEL_SCHEMA", f"duplicate interview topic: {requirement_id}")
        topics.append(requirement_id)

    for block, fragments in assessed_blocks:
        for requirement_id in block.requirement_ids:
            if requirement_id in gap_set:
                _fail(
                    "CLASSIFICATION_CONFLICT",
                    f"{block.block_id} links a requirement that is still classified as a gap: {requirement_id}",
                )
        warnings.extend(
            _check_generated_text(
                source,
                vocabulary,
                gap_phrase_set,
                gap_atom_set,
                block.block_id,
                block.text,
                fragments,
            )
        )

    evidence_by_requirement = {
        match.requirement_id: set(match.evidence_ids)
        for match in (*strong_matches, *partial_matches)
    }
    for block, _fragments in assessed_blocks:
        for requirement_id in block.requirement_ids:
            if not evidence_by_requirement.get(requirement_id, set()).intersection(block.source_fragment_ids):
                warnings.append(AdvisoryWarning(
                    "REQUIREMENT_LINKING_GAP",
                    block.block_id,
                    f"linked requirement {requirement_id} has no evidence overlap with this block",
                ))
    linked_requirements = {
        requirement_id
        for block, _fragments in assessed_blocks
        for requirement_id in block.requirement_ids
    }
    for match in (*strong_matches, *partial_matches):
        if match.requirement_id not in linked_requirements:
            warnings.append(AdvisoryWarning(
                "REQUIREMENT_NOT_EMPHASIZED",
                "",
                f"requirement {match.requirement_id} is not referenced by any generated block",
            ))

    generated_tokens = tuple(
        token for block, _fragments in assessed_blocks for token in _normalize_tokens(block.text)
    )
    for match in strong_matches:
        requirement = next(item for item in requirements if item.id == match.requirement_id)
        keywords = [
            token
            for token in _normalize_tokens(requirement.text)
            if len(token) >= 4 and token not in STOPWORDS
        ]
        if keywords and not any(_covers((token,), generated_tokens) for token in keywords):
            warnings.append(AdvisoryWarning(
                "KEYWORD_COVERAGE",
                "",
                f"strong requirement {match.requirement_id} appears in no generated text keyword",
            ))

    for first_index, first in enumerate(bullet_blocks):
        for second in bullet_blocks[first_index + 1 :]:
            if _near_duplicate(first, second):
                warnings.append(AdvisoryWarning(
                    "NEAR_DUPLICATE_BULLET",
                    first.block_id,
                    f"bullet text closely duplicates {second.block_id}",
                ))

    if total_bullets < MIN_TOTAL_BULLETS_TARGET:
        warnings.append(AdvisoryWarning(
            "LOW_BULLET_COVERAGE",
            "",
            f"only {total_bullets} experience bullets were generated",
        ))

    candidate_text_length = 0
    if headline is not None:
        candidate_text_length += len(headline.text)
    candidate_text_length += sum(len(item.text) for item in summaries)
    candidate_text_length += sum(
        len(f"{group.label}: {', '.join(group.items)}") for group in skill_groups
    )
    candidate_text_length += sum(len(item.text) for item in bullet_blocks)
    if candidate_text_length > MAX_TOTAL_CANDIDATE_CHARS:
        _fail("GENERATED_TEXT", f"generated candidate text exceeds {MAX_TOTAL_CANDIDATE_CHARS} characters")
    if candidate_text_length > RESUME_LENGTH_REVIEW_CHARS:
        warnings.append(AdvisoryWarning(
            "RESUME_LENGTH_REVIEW",
            "",
            f"generated candidate text is {candidate_text_length} characters",
        ))

    return ValidatedGeneration(
        SCHEMA_VERSION_V2,
        source.language,
        source.digest,
        company,
        role,
        headline,
        tuple(summaries),
        tuple(skill_groups),
        tuple(experience),
        tuple(rendered_ids),
        tuple(evidence_union),
        tuple(requirements),
        tuple(strong_matches),
        tuple(partial_matches),
        tuple(gaps),
        tuple(topics),
        tuple(warnings),
    )


def _validate_manifest_v2(source: SourceResume, manifest: dict[str, Any]) -> ValidatedGeneration:
    core = {
        "schema_version",
        "language",
        "source_digest",
        "target_company",
        "target_role",
        "summary",
        "skill_groups",
        "experience",
        "rendered_fragment_ids",
        "classification_evidence_ids",
        "vacancy_requirements",
        "classifications",
        "interview_topics",
        "warnings",
        "review_required",
    }
    allowed = core | {"headline"}
    unknown = sorted(set(manifest) - allowed)
    missing = sorted(core - set(manifest))
    if unknown or missing:
        _fail("MANIFEST", "v2 manifest fields are invalid")
    if (
        manifest["schema_version"] != SCHEMA_VERSION_V2
        or manifest["language"] != source.language
        or manifest["source_digest"] != source.digest
    ):
        _fail("MANIFEST", "manifest does not belong to the current source")
    if manifest["review_required"] is not True:
        _fail("MANIFEST", "v2 manifest must require human review")
    requirements_data = manifest["vacancy_requirements"]
    classifications = manifest["classifications"]
    if not isinstance(requirements_data, list) or not isinstance(classifications, dict):
        _fail("MANIFEST", "manifest requirement data is malformed")
    if any(not isinstance(item, dict) for item in requirements_data):
        _fail("MANIFEST", "manifest requirements must be objects")
    requirement_ids: set[str] = set()
    for item in requirements_data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            _fail("MANIFEST", "manifest requirements must contain string IDs")
        requirement_ids.add(item["id"])
    if set(classifications) != requirement_ids:
        _fail("MANIFEST", "manifest classifications do not match its requirements")
    raw_classifications: list[dict[str, Any]] = []
    for requirement in requirements_data:
        item = classifications.get(requirement.get("id"))
        if not isinstance(item, dict):
            _fail("MANIFEST", "missing requirement classification")
        status = item.get("status")
        if status not in {"strong", "partial", "gap"}:
            _fail("MANIFEST", "invalid requirement classification")
        _exact_keys(item, {"status", "evidence_ids"}, "manifest classification")
        if not isinstance(item["evidence_ids"], list):
            _fail("MANIFEST", "manifest evidence IDs must be an array")
        if status == "gap" and item["evidence_ids"] != []:
            _fail("MANIFEST", "gap classification cannot contain evidence")
        raw_classifications.append({
            "requirement_id": requirement["id"],
            "status": status,
            "evidence_ids": item["evidence_ids"],
        })
    if not all(isinstance(manifest[key], list) for key in ("summary", "skill_groups", "experience", "interview_topics")):
        _fail("MANIFEST", "manifest generated content is malformed")
    raw: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_V2,
        "target_company": manifest["target_company"],
        "target_role": manifest["target_role"],
        "vacancy_requirements": requirements_data,
        "requirement_classifications": raw_classifications,
        "summary": manifest["summary"],
        "skill_groups": [
            {
                "source_fragment_id": item.get("source_fragment_id") if isinstance(item, dict) else None,
                "items": item.get("items") if isinstance(item, dict) else None,
                "requirement_ids": item.get("requirement_ids", []) if isinstance(item, dict) else [],
            }
            for item in manifest["skill_groups"]
        ],
        "experience": [
            {
                "employer": item.get("employer") if isinstance(item, dict) else None,
                "bullets": item.get("bullets") if isinstance(item, dict) else None,
            }
            for item in manifest["experience"]
        ],
        "interview_topics": [{"requirement_id": item} for item in manifest["interview_topics"]],
    }
    if "headline" in manifest:
        raw["headline"] = manifest["headline"]
    generation = validate_generated_response(raw, source)
    expected_warnings = [
        {"code": item.code, "block_id": item.block_id, "message": item.message}
        for item in generation.warnings
    ]
    if manifest["warnings"] != expected_warnings:
        _fail("MANIFEST", "manifest warnings are inconsistent with deterministic validation")
    if list(manifest["rendered_fragment_ids"]) != list(generation.rendered_fragment_ids):
        _fail("MANIFEST", "manifest rendered fragment IDs are inconsistent")
    if list(manifest["classification_evidence_ids"]) != list(generation.classification_evidence_ids):
        _fail("MANIFEST", "manifest classification evidence IDs are inconsistent")
    if generation.target_company != manifest["target_company"] or generation.target_role != manifest["target_role"]:
        _fail("MANIFEST", "manifest target metadata is inconsistent")
    return generation


def validate_response(
    data: dict[str, Any],
    source: SourceResume,
) -> ValidatedSelection | ValidatedGeneration:
    if data.get("schema_version") == SCHEMA_VERSION_V2:
        return validate_generated_response(data, source)
    return _validate_response_v1(data, source)


REPORT_TEXT_V2 = {
    "en-US": {
        "overall": (
            "This report separates vacancy requirements from candidate evidence. Candidate-facing wording was "
            "adapted from cited master-resume fragments under deterministic fact checks and requires human review. "
            "It is not a match score or a guarantee."
        ),
        "vacancy": "Requirements are reproduced as sanitized vacancy data and are not candidate evidence.",
        "strong": "The following requirement has selected source evidence:",
        "partial": "The following requirement has related source evidence, but the exact requirement is not fully documented:",
        "gap": "No supporting candidate fragment was selected from the master resume.",
        "interview": "Discuss or verify this requirement during an interview; this topic does not assert that the candidate has the skill.",
        "evidence": "Exact source evidence used by this report:",
        "adapted": "Every generated block cites the source fragments it was adapted from:",
        "not_generated": "not generated; the master headline is rendered unchanged.",
        "skills_note": "Skill category labels and items are copied exactly from the master resume; only ordering and selection are adapted.",
        "block_evidence": "evidence:",
        "requirements": "requirements:",
        "changes": (
            "Candidate-facing wording (headline, summary, skills presentation, and experience bullets) was adapted "
            "from cited source fragments; identity, contact, employers, titles, dates, and education are rendered "
            "from the master resume."
        ),
        "warnings": "Advisory warnings require human review before using this resume:",
        "no_warnings": (
            "No advisory warnings were produced. Semantic equivalence of adapted wording still requires human review."
        ),
        "review": "Review every adapted block against its cited evidence before applying.",
    },
    "pt-BR": {
        "overall": (
            "Este relatório separa os requisitos da vaga das evidências do candidato. O texto voltado ao candidato "
            "foi adaptado de fragmentos citados do currículo mestre sob verificações determinísticas de fatos e "
            "exige revisão humana. Ele não é uma pontuação nem uma garantia."
        ),
        "vacancy": "Os requisitos são dados sanitizados da vaga e não constituem evidência do candidato.",
        "strong": "O requisito abaixo possui evidência selecionada da fonte:",
        "partial": "O requisito abaixo possui evidência relacionada, mas o requisito exato não está totalmente documentado:",
        "gap": "Nenhum fragmento de suporte foi selecionado do currículo mestre.",
        "interview": "Discutir ou verificar este requisito em entrevista; este tópico não afirma que o candidato possui a habilidade.",
        "evidence": "Evidências exatas da fonte usadas por este relatório:",
        "adapted": "Cada bloco gerado cita os fragmentos de origem dos quais foi adaptado:",
        "not_generated": "não gerado; a manchete do currículo mestre é renderizada sem alterações.",
        "skills_note": "Rótulos e itens das categorias de habilidades são copiados exatamente do currículo mestre; apenas a ordem e a seleção foram adaptadas.",
        "block_evidence": "evidências:",
        "requirements": "requisitos:",
        "changes": (
            "O texto voltado ao candidato (manchete, resumo, apresentação de habilidades e bullets de experiência) "
            "foi adaptado de fragmentos citados da fonte; identidade, contato, empresas, cargos, datas e formação são "
            "renderizados do currículo mestre."
        ),
        "warnings": "Avisos de revisão humana obrigatória antes de usar este currículo:",
        "no_warnings": (
            "Nenhum aviso foi produzido. A equivalência semântica do texto adaptado ainda exige revisão humana."
        ),
        "review": "Revise cada bloco adaptado contra as evidências citadas antes de se candidatar.",
    },
}


def _generated_block_lines(
    block: GeneratedBlock,
    text: dict[str, str],
) -> list[str]:
    lines = [f"- `{block.block_id}`: \"{block.text}\""]
    lines.append("  " + text["block_evidence"] + " " + ", ".join(f"`{item}`" for item in block.source_fragment_ids))
    if block.requirement_ids:
        lines.append("  " + text["requirements"] + " " + ", ".join(f"`{item}`" for item in block.requirement_ids))
    return lines


def render_generated_resume(source: SourceResume, generation: ValidatedGeneration) -> str:
    if generation.source_digest != source.digest or generation.language != source.language:
        _fail("MANIFEST", "generation does not belong to the current source")
    fragments = source.fragments
    blocks: list[str] = []
    headline_text = generation.headline.text if generation.headline is not None else fragments["headline"].text
    blocks.append("\n".join([
        fragments["identity.name"].text,
        headline_text,
        fragments["contact.location"].text,
        fragments["contact.links"].text,
    ]))

    summary_title, skills_title, experience_title, education_title = EXPECTED_SECTIONS[source.language]
    blocks.append(fragments[source.section_ids[summary_title]].text)
    blocks.extend(item.text for item in generation.summaries)

    blocks.append(fragments[source.section_ids[skills_title]].text)
    for group in generation.skill_groups:
        blocks.append(f"**{group.label}:** {', '.join(group.items)}")

    blocks.append(fragments[source.section_ids[experience_title]].text)
    entries = {item.employer_key: item for item in generation.experience}
    for employer in source.employers:
        entry = entries[employer.key]
        employer_blocks: list[str] = []
        for group in employer.header_groups:
            employer_blocks.append("\n".join(fragments[item].text for item in group))
        employer_blocks.extend(f"- {bullet.text}" for bullet in entry.bullets)
        blocks.append("\n\n".join(employer_blocks))

    blocks.append(fragments[source.section_ids[education_title]].text)
    for entry in source.education:
        blocks.append("\n".join(fragments[item].text for item in entry.fragment_ids))
    return _render_blocks(blocks)


def render_generated_report(source: SourceResume, generation: ValidatedGeneration) -> str:
    if generation.source_digest != source.digest or generation.language != source.language:
        _fail("MANIFEST", "generation does not belong to the current source")
    text = REPORT_TEXT_V2[source.language]
    requirements = {item.id: item for item in generation.requirements}
    strong = {item.requirement_id: item for item in generation.strong_matches}
    partial = {item.requirement_id: item for item in generation.partial_matches}
    lines = [
        f"# Match report: {escape_markdown(generation.target_company)} | {escape_markdown(generation.target_role)}",
        "",
        "## Overall assessment",
        "",
        text["overall"],
        "",
        "## Vacancy requirements",
        "",
        text["vacancy"],
    ]
    for requirement in generation.requirements:
        lines.append(f"- `{requirement.id}` [{requirement.priority}] {escape_markdown(requirement.text)}")

    lines.extend(["", "## Strong matches", ""])
    if not strong:
        lines.append("- None classified.")
    for requirement_id, match in strong.items():
        requirement = requirements[requirement_id]
        lines.extend([f"- `{requirement.id}`: {escape_markdown(requirement.text)}", f"  {text['strong']}"])
        lines.extend(_evidence_block(source, match.evidence_ids))

    lines.extend(["", "## Partial matches", ""])
    if not partial:
        lines.append("- None classified.")
    for requirement_id, match in partial.items():
        requirement = requirements[requirement_id]
        lines.extend([f"- `{requirement.id}`: {escape_markdown(requirement.text)}", f"  {text['partial']}"])
        lines.extend(_evidence_block(source, match.evidence_ids))

    lines.extend(["", "## Gaps", ""])
    if not generation.gaps:
        lines.append("- None classified.")
    for requirement_id in generation.gaps:
        requirement = requirements[requirement_id]
        lines.append(f"- `{requirement.id}`: {escape_markdown(requirement.text)}. {text['gap']}")

    lines.extend(["", "## Adapted candidate-facing content", "", text["adapted"]])
    if generation.headline is None:
        lines.append(f"- Headline: {text['not_generated']}")
    else:
        lines.extend(_generated_block_lines(generation.headline, text))
    for block in generation.summaries:
        lines.extend(_generated_block_lines(block, text))
    lines.append(f"- {text['skills_note']}")
    for group in generation.skill_groups:
        lines.append(f"- `{group.source_fragment_id}`: **{group.label}:** {', '.join(group.items)}")
        if group.requirement_ids:
            lines.append("  " + text["requirements"] + " " + ", ".join(f"`{item}`" for item in group.requirement_ids))
    for entry in generation.experience:
        for bullet in entry.bullets:
            lines.extend(_generated_block_lines(bullet, text))

    generated_block_count = (1 if generation.headline is not None else 0) + len(generation.summaries)
    generated_block_count += sum(len(entry.bullets) for entry in generation.experience)
    lines.extend([
        "",
        "## Changes made",
        "",
        f"- {text['changes']}",
        f"- Generated blocks: {generated_block_count}.",
        f"- Advisory warnings: {len(generation.warnings)}.",
        f"- {text['review']}",
    ])

    lines.extend(["", "## Advisory warnings", ""])
    if not generation.warnings:
        lines.append(f"- {text['no_warnings']}")
    else:
        lines.append(f"- {text['warnings']}")
        for warning in generation.warnings:
            where = f" `{warning.block_id}`" if warning.block_id else ""
            lines.append(f"  - `{warning.code}`{where}: {escape_markdown(warning.message)}")

    lines.extend(["", "## Interview points", ""])
    if not generation.interview_topics:
        lines.append("- None classified.")
    for requirement_id in generation.interview_topics:
        requirement = requirements[requirement_id]
        lines.append(f"- `{requirement.id}`: {escape_markdown(requirement.text)}. {text['interview']}")

    used_evidence: list[str] = []
    for evidence_id in (*generation.rendered_fragment_ids, *generation.classification_evidence_ids):
        if evidence_id not in used_evidence:
            used_evidence.append(evidence_id)
    lines.extend(["", "## Evidence index", "", text["evidence"]])
    if not used_evidence:
        lines.append("- None.")
    else:
        lines.extend(_evidence_block(source, used_evidence))
    return "\n".join(lines).rstrip() + "\n"


def parse_and_validate_response(raw: str, source: SourceResume) -> ValidatedSelection | ValidatedGeneration:
    return validate_response(_strict_json(raw), source)


def _render_blocks(blocks: Iterable[str]) -> str:
    values = [item.strip("\n") for item in blocks if item.strip()]
    if not values:
        _fail("RENDER", "rendered section is empty")
    return "\n\n".join(values) + "\n"


def render_resume(source: SourceResume, selection: ValidatedSelection | ValidatedGeneration) -> str:
    if getattr(selection, "schema_version", SCHEMA_VERSION) == SCHEMA_VERSION_V2:
        return render_generated_resume(source, selection)
    if selection.source_digest != source.digest or selection.language != source.language:
        _fail("MANIFEST", "selection does not belong to the current source")
    fragments = source.fragments
    blocks: list[str] = []
    preamble = [fragments[item].text for item in source.preamble_ids]
    blocks.append("\n".join(preamble))

    blocks.append(fragments[source.section_ids[EXPECTED_SECTIONS[source.language][0]]].text)
    selected_summaries = set(item for item in selection.selected_fragment_ids if fragments[item].kind == "summary")
    blocks.extend(fragments[item].text for item in source.summary_ids if item in selected_summaries)

    skills = [item for item in selection.selected_fragment_ids if fragments[item].kind == "skill"]
    blocks.append(fragments[source.section_ids[EXPECTED_SECTIONS[source.language][1]]].text)
    blocks.extend(fragments[item].text for item in skills)

    experience_title = EXPECTED_SECTIONS[source.language][2]
    blocks.append(fragments[source.section_ids[experience_title]].text)
    selected_set = set(selection.selected_fragment_ids)
    for employer in source.employers:
        employer_blocks: list[str] = []
        for group in employer.header_groups:
            employer_blocks.append("\n".join(fragments[item].text for item in group))
        selected_bullets = [item for item in employer.bullet_ids if item in selected_set]
        employer_blocks.extend(fragments[item].text for item in selected_bullets)
        blocks.append("\n\n".join(employer_blocks))

    education_title = EXPECTED_SECTIONS[source.language][3]
    blocks.append(fragments[source.section_ids[education_title]].text)
    for entry in source.education:
        blocks.append("\n".join(fragments[item].text for item in entry.fragment_ids))
    return _render_blocks(blocks)


REPORT_TEXT = {
    "en-US": {
        "overall": "This report separates vacancy requirements from candidate evidence. It is not a match score or a guarantee.",
        "vacancy": "Requirements are reproduced as sanitized vacancy data and are not candidate evidence.",
        "strong": "The following requirement has selected source evidence:",
        "partial": "The following requirement has related source evidence, but the exact requirement is not fully documented:",
        "gap": "No supporting candidate fragment was selected from the master resume.",
        "changes": "The resume was composed from exact source fragments; no candidate-facing sentence was rewritten.",
        "interview": "Discuss or verify this requirement during an interview; this topic does not assert that the candidate has the skill.",
        "evidence": "Exact source evidence used by this report:",
    },
    "pt-BR": {
        "overall": "Este relatório separa os requisitos da vaga das evidências do candidato. Ele não é uma pontuação nem uma garantia.",
        "vacancy": "Os requisitos são dados sanitizados da vaga e não constituem evidência do candidato.",
        "strong": "O requisito abaixo possui evidência selecionada da fonte:",
        "partial": "O requisito abaixo possui evidência relacionada, mas o requisito exato não está totalmente documentado:",
        "gap": "Nenhum fragmento de suporte foi selecionado do currículo mestre.",
        "changes": "O currículo foi composto por fragmentos exatos da fonte; nenhuma frase voltada ao candidato foi reescrita.",
        "interview": "Discutir ou verificar este requisito em entrevista; este tópico não afirma que o candidato possui a habilidade.",
        "evidence": "Evidências exatas da fonte usadas por este relatório:",
    },
}


def _evidence_block(source: SourceResume, evidence_ids: Iterable[str]) -> list[str]:
    lines: list[str] = []
    for evidence_id in evidence_ids:
        fragment = source.fragments[evidence_id]
        lines.extend(
            [
                f"- Fragment `{evidence_id}`:",
                "  ```text",
                *(f"  {line}" for line in fragment.text.splitlines()),
                "  ```",
            ]
        )
    return lines


def render_report(source: SourceResume, selection: ValidatedSelection | ValidatedGeneration) -> str:
    if getattr(selection, "schema_version", SCHEMA_VERSION) == SCHEMA_VERSION_V2:
        return render_generated_report(source, selection)
    if selection.source_digest != source.digest or selection.language != source.language:
        _fail("MANIFEST", "selection does not belong to the current source")
    text = REPORT_TEXT[source.language]
    requirements = {item.id: item for item in selection.requirements}
    strong = {item.requirement_id: item for item in selection.strong_matches}
    partial = {item.requirement_id: item for item in selection.partial_matches}
    lines = [
        f"# Match report: {escape_markdown(selection.target_company)} | {escape_markdown(selection.target_role)}",
        "",
        "## Overall assessment",
        "",
        text["overall"],
        "",
        "## Vacancy requirements",
        "",
        text["vacancy"],
    ]
    for requirement in selection.requirements:
        lines.append(f"- `{requirement.id}` [{requirement.priority}] {escape_markdown(requirement.text)}")

    lines.extend(["", "## Strong matches", ""])
    if not strong:
        lines.append("- None classified.")
    for requirement_id, match in strong.items():
        requirement = requirements[requirement_id]
        lines.extend([f"- `{requirement.id}`: {escape_markdown(requirement.text)}", f"  {text['strong']}"])
        lines.extend(_evidence_block(source, match.evidence_ids))

    lines.extend(["", "## Partial matches", ""])
    if not partial:
        lines.append("- None classified.")
    for requirement_id, match in partial.items():
        requirement = requirements[requirement_id]
        lines.extend([f"- `{requirement.id}`: {escape_markdown(requirement.text)}", f"  {text['partial']}"])
        lines.extend(_evidence_block(source, match.evidence_ids))

    lines.extend(["", "## Gaps", ""])
    if not selection.gaps:
        lines.append("- None classified.")
    for requirement_id in selection.gaps:
        requirement = requirements[requirement_id]
        lines.append(f"- `{requirement.id}`: {escape_markdown(requirement.text)}. {text['gap']}")

    lines.extend([
        "",
        "## Changes made",
        "",
        f"- {text['changes']}",
        f"- Selected source fragments: {len(selection.selected_fragment_ids)}.",
        "",
        "## Interview points",
        "",
    ])
    if not selection.interview_topics:
        lines.append("- None classified.")
    for requirement_id in selection.interview_topics:
        requirement = requirements[requirement_id]
        lines.append(f"- `{requirement.id}`: {escape_markdown(requirement.text)}. {text['interview']}")

    used_evidence: list[str] = []
    for match in (*selection.strong_matches, *selection.partial_matches):
        for evidence_id in match.evidence_ids:
            if evidence_id not in used_evidence:
                used_evidence.append(evidence_id)
    lines.extend(["", "## Evidence index", "", text["evidence"]])
    if not used_evidence:
        lines.append("- None.")
    else:
        lines.extend(_evidence_block(source, used_evidence))
    return "\n".join(lines).rstrip() + "\n"


def plain_markdown(text: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    value = re.sub(r"[*_`#]", "", value)
    value = re.sub(r"^\s*[-+]\s+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


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
    """Normalize extracted text for exact fragment matching.

    NFKC folds ligatures and compatibility forms, invisible extraction
    characters such as soft hyphens and zero-width spaces are removed, and
    line wrapping plus harmless punctuation spacing are collapsed. Source
    wording and token order are preserved.
    """

    return _collapse_extracted_whitespace(_canonical_extracted(text))


def extracted_text_variants(text: str) -> tuple[str, ...]:
    """Return deterministic normalized variants of PDF-extracted text.

    Line-break hyphenation may be extracted with the hyphen preserved or with
    the split word joined. Both canonical forms are returned so exact source
    fragments can match either representation without fuzzy similarity or
    substring heuristics.
    """

    canonical = _canonical_extracted(text)
    preserved = _collapse_extracted_whitespace(LINE_BREAK_HYPHEN_KEEP_RE.sub("-", canonical))
    joined = _collapse_extracted_whitespace(LINE_BREAK_HYPHEN_DROP_RE.sub("", canonical))
    variants = [preserved]
    if joined != preserved:
        variants.append(joined)
    return tuple(variants)


def validate_rendered_markdown(source: SourceResume, selection: ValidatedSelection, markdown: str) -> None:
    expected = render_resume(source, selection)
    if markdown != expected:
        _fail("MARKDOWN_PROVENANCE", "rendered Markdown differs from deterministic source composition")
    if not markdown.strip():
        _fail("MARKDOWN_PROVENANCE", "rendered Markdown is unexpectedly empty")
    if re.search(r"(?im)^(?:```|~~~|<|---$)", markdown):
        _fail("MARKDOWN_PROVENANCE", "rendered resume contains an ATS-unsafe structure")


def validate_rendered_report(source: SourceResume, selection: ValidatedSelection, report: str) -> None:
    expected = render_report(source, selection)
    if report != expected:
        _fail("REPORT_PROVENANCE", "match report differs from deterministic template rendering")


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("MANIFEST", f"cannot read manifest: {exc}")
    if not isinstance(data, dict):
        _fail("MANIFEST", "manifest must be an object")
    return data


def validate_manifest(source: SourceResume, manifest: dict[str, Any]) -> ValidatedSelection | ValidatedGeneration:
    if manifest.get("schema_version") == SCHEMA_VERSION_V2:
        return _validate_manifest_v2(source, manifest)
    required = {
        "schema_version",
        "language",
        "source_digest",
        "target_company",
        "target_role",
        "selected_fragment_ids",
        "vacancy_requirements",
        "classifications",
        "interview_topics",
    }
    _exact_keys(manifest, required, "manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION or manifest["language"] != source.language or manifest["source_digest"] != source.digest:
        _fail("MANIFEST", "manifest does not belong to the current source")
    requirements_data = manifest["vacancy_requirements"]
    classifications = manifest["classifications"]
    if not isinstance(requirements_data, list) or not isinstance(classifications, dict):
        _fail("MANIFEST", "manifest requirement data is malformed")
    if any(not isinstance(item, dict) for item in requirements_data):
        _fail("MANIFEST", "manifest requirements must be objects")
    requirement_ids: set[str] = set()
    for item in requirements_data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            _fail("MANIFEST", "manifest requirements must contain string IDs")
        requirement_ids.add(item["id"])
    if set(classifications) != requirement_ids:
        _fail("MANIFEST", "manifest classifications do not match its requirements")
    raw: dict[str, Any] = {
        "schema_version": manifest["schema_version"],
        "target_company": manifest["target_company"],
        "target_role": manifest["target_role"],
        "selected_fragment_ids": manifest["selected_fragment_ids"],
        "vacancy_requirements": requirements_data,
        "requirement_classifications": [],
        "interview_topics": [{"requirement_id": item} for item in manifest["interview_topics"]],
    }
    for requirement in requirements_data:
        item = classifications.get(requirement.get("id"))
        if not isinstance(item, dict):
            _fail("MANIFEST", "missing requirement classification")
        status = item.get("status")
        if status not in {"strong", "partial", "gap"}:
            _fail("MANIFEST", "invalid requirement classification")
        _exact_keys(item, {"status", "evidence_ids"}, "manifest classification")
        if not isinstance(item["evidence_ids"], list):
            _fail("MANIFEST", "manifest evidence IDs must be an array")
        if status == "gap" and item["evidence_ids"] != []:
            _fail("MANIFEST", "gap classification cannot contain evidence")
        raw["requirement_classifications"].append({
            "requirement_id": requirement["id"],
            "status": status,
            "evidence_ids": item["evidence_ids"],
        })
    selection = validate_response(raw, source)
    if selection.target_company != manifest["target_company"] or selection.target_role != manifest["target_role"]:
        _fail("MANIFEST", "manifest target metadata is inconsistent")
    return selection


def write_manifest(path: Path, selection: ValidatedSelection | ValidatedGeneration) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(selection.to_manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
