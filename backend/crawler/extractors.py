"""Extraction and transformation layer for raw OpenAlex API responses.

Converts raw API dicts into typed domain dataclasses and extracts graph
relationships ready for writing to Postgres and Neo4j.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def strip_openalex_id(url: str | None) -> str | None:
    """Strip the OpenAlex URL prefix and return the bare prefixed ID.

    ``"https://openalex.org/W1234"`` -> ``"W1234"``

    Returns ``None`` if the input is ``None`` or empty.
    """
    if not url:
        return None
    return url.rsplit("/", 1)[-1]


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Reconstruct plain text from an OpenAlex abstract inverted index.

    OpenAlex stores abstracts as ``{word: [positions]}`` mappings due to
    licensing constraints.  Each word is placed at its position(s) in a flat
    slot array, then the slots are joined with spaces.

    Returns ``None`` when the index is ``None`` or contains no entries.
    """
    if not inverted_index:
        return None

    pairs: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            pairs.append((pos, word))

    if not pairs:
        return None

    pairs.sort(key=lambda p: p[0])

    max_pos = pairs[-1][0]
    slots: list[str] = [""] * (max_pos + 1)
    for pos, word in pairs:
        slots[pos] = word

    return " ".join(w for w in slots if w)


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Paper:
    id: str
    title: str | None
    abstract: str | None
    publication_year: int | None
    doi: str | None
    citation_count: int
    source: str = "openalex"


@dataclass
class Author:
    id: str
    name: str | None
    orcid: str | None = None
    works_count: int = 0
    citation_count: int = 0


@dataclass
class Institution:
    id: str
    name: str | None
    country: str | None
    institution_type: str | None


@dataclass
class Topic:
    id: str
    name: str | None
    domain: str | None = None
    field: str | None = None


# ---------------------------------------------------------------------------
# Relationship dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Authorship:
    author_id: str
    paper_id: str


@dataclass
class Affiliation:
    author_id: str
    institution_id: str
    start_year: int | None = None
    end_year: int | None = None
    # True when this is the first-listed author's first institution.
    # Works-endpoint data does not carry employment dates, so temporal fields
    # are always None at this stage.
    primary: bool = False


@dataclass
class Citation:
    source_paper_id: str
    cited_paper_id: str


@dataclass
class PaperTopic:
    paper_id: str
    topic_id: str


# ---------------------------------------------------------------------------
# Entity extractors
# ---------------------------------------------------------------------------


def extract_paper(raw: dict[str, Any]) -> Paper:
    """Extract a :class:`Paper` from a raw OpenAlex work dict."""
    paper_id = strip_openalex_id(raw.get("id"))
    if not paper_id:
        raise ValueError(f"Work missing 'id': {raw!r}")

    doi_url: str | None = raw.get("doi")
    doi: str | None = None
    if doi_url:
        doi = doi_url.removeprefix("https://doi.org/") or None

    return Paper(
        id=paper_id,
        title=raw.get("title"),
        abstract=reconstruct_abstract(raw.get("abstract_inverted_index")),
        publication_year=raw.get("publication_year"),
        doi=doi,
        citation_count=raw.get("cited_by_count") or 0,
        source="openalex",
    )


def extract_author(raw: dict[str, Any]) -> Author:
    """Extract an :class:`Author` from an OpenAlex author object.

    Handles both the minimal author object embedded in authorship records
    (only ``id`` and ``display_name``) and full author API responses that
    carry ``orcid``, ``works_count``, and ``cited_by_count``.
    """
    author_id = strip_openalex_id(raw.get("id"))
    if not author_id:
        raise ValueError(f"Author missing 'id': {raw!r}")

    orcid_url: str | None = raw.get("orcid")
    orcid: str | None = None
    if orcid_url:
        orcid = orcid_url.removeprefix("https://orcid.org/") or None

    return Author(
        id=author_id,
        name=raw.get("display_name"),
        orcid=orcid,
        works_count=raw.get("works_count") or 0,
        citation_count=raw.get("cited_by_count") or 0,
    )


def extract_institution(raw: dict[str, Any]) -> Institution:
    """Extract an :class:`Institution` from an OpenAlex institution object."""
    institution_id = strip_openalex_id(raw.get("id"))
    if not institution_id:
        raise ValueError(f"Institution missing 'id': {raw!r}")

    return Institution(
        id=institution_id,
        name=raw.get("display_name"),
        # Embedded institution objects use country_code; standalone API uses country_code too.
        country=raw.get("country_code") or raw.get("country"),
        institution_type=raw.get("type"),
    )


def extract_topic(raw: dict[str, Any]) -> Topic:
    """Extract a :class:`Topic` from an OpenAlex topic object.

    Embedded topic objects (from works) carry only ``id`` and
    ``display_name``; standalone topic API responses also include ``domain``
    and ``field`` sub-objects.
    """
    topic_id = strip_openalex_id(raw.get("id"))
    if not topic_id:
        raise ValueError(f"Topic missing 'id': {raw!r}")

    domain_raw = raw.get("domain")
    field_raw = raw.get("field")

    domain: str | None = domain_raw.get("display_name") if isinstance(domain_raw, dict) else domain_raw
    field: str | None = field_raw.get("display_name") if isinstance(field_raw, dict) else field_raw

    return Topic(
        id=topic_id,
        name=raw.get("display_name"),
        domain=domain,
        field=field,
    )


# ---------------------------------------------------------------------------
# Relationship extractors
# ---------------------------------------------------------------------------


def extract_authorships(raw_work: dict[str, Any]) -> list[Authorship]:
    """Extract :class:`Authorship` edges from a raw work dict."""
    paper_id = strip_openalex_id(raw_work.get("id"))
    if not paper_id:
        raise ValueError(f"Work missing 'id': {raw_work!r}")

    result: list[Authorship] = []
    for authorship in raw_work.get("authorships") or []:
        author_raw = authorship.get("author") or {}
        author_id = strip_openalex_id(author_raw.get("id"))
        if author_id:
            result.append(Authorship(author_id=author_id, paper_id=paper_id))
    return result


def extract_citations(raw_work: dict[str, Any]) -> list[Citation]:
    """Extract :class:`Citation` edges from a raw work dict.

    OpenAlex ``referenced_works`` lists papers that *this* work cites, so the
    directionality is ``source_paper_id -> cited_paper_id``.
    """
    paper_id = strip_openalex_id(raw_work.get("id"))
    if not paper_id:
        raise ValueError(f"Work missing 'id': {raw_work!r}")

    result: list[Citation] = []
    for ref_url in raw_work.get("referenced_works") or []:
        cited_id = strip_openalex_id(ref_url)
        if cited_id:
            result.append(Citation(source_paper_id=paper_id, cited_paper_id=cited_id))
    return result


def extract_paper_topics(raw_work: dict[str, Any]) -> list[PaperTopic]:
    """Extract :class:`PaperTopic` edges from a raw work dict."""
    paper_id = strip_openalex_id(raw_work.get("id"))
    if not paper_id:
        raise ValueError(f"Work missing 'id': {raw_work!r}")

    result: list[PaperTopic] = []
    for topic_raw in raw_work.get("topics") or []:
        topic_id = strip_openalex_id(topic_raw.get("id"))
        if topic_id:
            result.append(PaperTopic(paper_id=paper_id, topic_id=topic_id))
    return result


def extract_affiliations(raw_work: dict[str, Any]) -> list[Affiliation]:
    """Extract :class:`Affiliation` edges from a raw work dict.

    Maps each author to each of their institutions for this work.  Since the
    works endpoint does not carry employment date ranges, ``start_year`` and
    ``end_year`` are always ``None`` here.  The ``primary`` flag is ``True``
    only for the first institution of the first-position author.
    """
    result: list[Affiliation] = []
    for authorship in raw_work.get("authorships") or []:
        author_raw = authorship.get("author") or {}
        author_id = strip_openalex_id(author_raw.get("id"))
        if not author_id:
            continue

        is_first_author = authorship.get("author_position") == "first"

        for idx, institution_raw in enumerate(authorship.get("institutions") or []):
            institution_id = strip_openalex_id(institution_raw.get("id"))
            if institution_id:
                result.append(
                    Affiliation(
                        author_id=author_id,
                        institution_id=institution_id,
                        start_year=None,
                        end_year=None,
                        primary=is_first_author and idx == 0,
                    )
                )
    return result
