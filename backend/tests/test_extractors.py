"""Unit tests for the extraction and transformation layer.

All tests run purely in-process against fixture JSON files — no network or
database calls are made.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from crawler.extractors import (
    Affiliation,
    Authorship,
    Author,
    Citation,
    Institution,
    Paper,
    PaperTopic,
    Topic,
    extract_affiliations,
    extract_author,
    extract_authorships,
    extract_citations,
    extract_institution,
    extract_paper,
    extract_paper_topics,
    extract_topic,
    reconstruct_abstract,
    strip_openalex_id,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _work(idx: int = 0) -> dict[str, Any]:
    """Return the work at position *idx* from works_page_1.json."""
    return load_fixture("works_page_1.json")["results"][idx]


# ---------------------------------------------------------------------------
# strip_openalex_id
# ---------------------------------------------------------------------------


class TestStripOpenAlexId:
    def test_strips_url_prefix(self) -> None:
        assert strip_openalex_id("https://openalex.org/W2741809807") == "W2741809807"

    def test_strips_author_prefix(self) -> None:
        assert strip_openalex_id("https://openalex.org/A2109829088") == "A2109829088"

    def test_strips_institution_prefix(self) -> None:
        assert strip_openalex_id("https://openalex.org/I1289349794") == "I1289349794"

    def test_strips_topic_prefix(self) -> None:
        assert strip_openalex_id("https://openalex.org/T10116") == "T10116"

    def test_returns_none_for_none(self) -> None:
        assert strip_openalex_id(None) is None

    def test_returns_none_for_empty_string(self) -> None:
        assert strip_openalex_id("") is None

    def test_bare_id_returned_as_is(self) -> None:
        assert strip_openalex_id("W2741809807") == "W2741809807"


# ---------------------------------------------------------------------------
# reconstruct_abstract
# ---------------------------------------------------------------------------


class TestReconstructAbstract:
    def test_reconstructs_simple_abstract(self) -> None:
        index = {"Hello": [0], "world": [1]}
        assert reconstruct_abstract(index) == "Hello world"

    def test_reconstructs_word_at_multiple_positions(self) -> None:
        index = {"the": [0, 3], "cat": [1], "sat": [2]}
        result = reconstruct_abstract(index)
        assert result == "the cat sat the"

    def test_returns_none_for_none(self) -> None:
        assert reconstruct_abstract(None) is None

    def test_returns_none_for_empty_dict(self) -> None:
        assert reconstruct_abstract({}) is None

    def test_reconstructs_from_fixture(self) -> None:
        work = _work(0)  # "Attention Is All You Need"
        index = work["abstract_inverted_index"]
        result = reconstruct_abstract(index)
        assert result is not None
        assert "dominant" in result
        assert "sequence" in result
        assert "attention" in result

    def test_null_abstract_in_fixture(self) -> None:
        work = _work(2)  # "Language Models are Few-Shot Learners" — null abstract
        assert work["abstract_inverted_index"] is None
        assert reconstruct_abstract(work["abstract_inverted_index"]) is None

    def test_word_order_is_correct(self) -> None:
        index = {"B": [1], "A": [0], "C": [2]}
        assert reconstruct_abstract(index) == "A B C"

    def test_gap_in_positions_skips_empty_slots(self) -> None:
        # Position 1 is unused — the slot is empty and filtered out.
        index = {"A": [0], "C": [2]}
        result = reconstruct_abstract(index)
        assert result == "A C"


# ---------------------------------------------------------------------------
# extract_paper
# ---------------------------------------------------------------------------


class TestExtractPaper:
    def test_extracts_id(self) -> None:
        paper = extract_paper(_work(0))
        assert paper.id == "W2741809807"

    def test_extracts_title(self) -> None:
        paper = extract_paper(_work(0))
        assert paper.title == "Attention Is All You Need"

    def test_extracts_abstract(self) -> None:
        paper = extract_paper(_work(0))
        assert paper.abstract is not None
        assert len(paper.abstract) > 0

    def test_null_abstract_becomes_none(self) -> None:
        paper = extract_paper(_work(2))
        assert paper.abstract is None

    def test_extracts_publication_year(self) -> None:
        paper = extract_paper(_work(0))
        assert paper.publication_year == 2017

    def test_strips_doi_prefix(self) -> None:
        paper = extract_paper(_work(0))
        assert paper.doi == "10.48550/arXiv.1706.03762"
        assert "https://doi.org/" not in (paper.doi or "")

    def test_null_doi_becomes_none(self) -> None:
        paper = extract_paper(_work(2))
        assert paper.doi is None

    def test_extracts_citation_count(self) -> None:
        paper = extract_paper(_work(0))
        assert paper.citation_count == 120000

    def test_source_is_openalex(self) -> None:
        paper = extract_paper(_work(0))
        assert paper.source == "openalex"

    def test_returns_paper_dataclass(self) -> None:
        assert isinstance(extract_paper(_work(0)), Paper)

    def test_raises_on_missing_id(self) -> None:
        with pytest.raises(ValueError, match="missing 'id'"):
            extract_paper({"title": "No ID here"})


# ---------------------------------------------------------------------------
# extract_author
# ---------------------------------------------------------------------------


class TestExtractAuthor:
    def test_extracts_minimal_author_from_authorship(self) -> None:
        raw = {"id": "https://openalex.org/A2109829088", "display_name": "Ashish Vaswani"}
        author = extract_author(raw)
        assert author.id == "A2109829088"
        assert author.name == "Ashish Vaswani"
        assert author.orcid is None
        assert author.works_count == 0
        assert author.citation_count == 0

    def test_extracts_full_author_from_fixture(self) -> None:
        raw = load_fixture("author.json")
        author = extract_author(raw)
        assert author.id == "A2109829088"
        assert author.name == "Ashish Vaswani"
        assert author.orcid == "0000-0002-5251-5425"
        assert author.works_count == 42
        assert author.citation_count == 145000

    def test_strips_orcid_url_prefix(self) -> None:
        raw = {
            "id": "https://openalex.org/A123",
            "display_name": "Test",
            "orcid": "https://orcid.org/0000-0001-2345-6789",
        }
        author = extract_author(raw)
        assert author.orcid == "0000-0001-2345-6789"

    def test_returns_author_dataclass(self) -> None:
        assert isinstance(extract_author({"id": "https://openalex.org/A1", "display_name": "X"}), Author)

    def test_raises_on_missing_id(self) -> None:
        with pytest.raises(ValueError, match="missing 'id'"):
            extract_author({"display_name": "No ID"})

    def test_null_cited_by_count_defaults_to_zero(self) -> None:
        raw = {"id": "https://openalex.org/A1", "display_name": "X", "cited_by_count": None}
        assert extract_author(raw).citation_count == 0


# ---------------------------------------------------------------------------
# extract_institution
# ---------------------------------------------------------------------------


class TestExtractInstitution:
    def test_extracts_from_embedded_object(self) -> None:
        raw = {
            "id": "https://openalex.org/I1289349794",
            "display_name": "Google Brain",
            "country_code": "US",
            "type": "company",
        }
        inst = extract_institution(raw)
        assert inst.id == "I1289349794"
        assert inst.name == "Google Brain"
        assert inst.country == "US"
        assert inst.institution_type == "company"

    def test_extracts_from_fixture(self) -> None:
        raw = load_fixture("institution.json")
        inst = extract_institution(raw)
        assert inst.id == "I27837315"
        assert inst.name == "Massachusetts Institute of Technology"
        assert inst.country == "US"
        assert inst.institution_type == "education"

    def test_returns_institution_dataclass(self) -> None:
        assert isinstance(
            extract_institution({"id": "https://openalex.org/I1", "display_name": "X"}),
            Institution,
        )

    def test_raises_on_missing_id(self) -> None:
        with pytest.raises(ValueError, match="missing 'id'"):
            extract_institution({"display_name": "No ID"})

    def test_missing_country_is_none(self) -> None:
        raw = {"id": "https://openalex.org/I1", "display_name": "Unknown Org"}
        assert extract_institution(raw).country is None


# ---------------------------------------------------------------------------
# extract_topic
# ---------------------------------------------------------------------------


class TestExtractTopic:
    def test_extracts_minimal_topic(self) -> None:
        raw = {"id": "https://openalex.org/T10116", "display_name": "Neural Machine Translation"}
        topic = extract_topic(raw)
        assert topic.id == "T10116"
        assert topic.name == "Neural Machine Translation"
        assert topic.domain is None
        assert topic.field is None

    def test_extracts_full_topic_from_fixture(self) -> None:
        raw = load_fixture("topic.json")
        topic = extract_topic(raw)
        assert topic.id == "T10116"
        assert topic.name == "Neural Machine Translation"
        assert topic.domain == "Computer Science"
        assert topic.field == "Artificial Intelligence"

    def test_returns_topic_dataclass(self) -> None:
        assert isinstance(extract_topic({"id": "https://openalex.org/T1", "display_name": "X"}), Topic)

    def test_raises_on_missing_id(self) -> None:
        with pytest.raises(ValueError, match="missing 'id'"):
            extract_topic({"display_name": "No ID"})

    def test_domain_as_plain_string(self) -> None:
        raw = {"id": "https://openalex.org/T1", "display_name": "X", "domain": "Science"}
        assert extract_topic(raw).domain == "Science"


# ---------------------------------------------------------------------------
# extract_authorships
# ---------------------------------------------------------------------------


class TestExtractAuthorships:
    def test_extracts_authorships(self) -> None:
        result = extract_authorships(_work(0))
        assert len(result) == 2
        assert all(isinstance(a, Authorship) for a in result)
        author_ids = {a.author_id for a in result}
        assert "A2109829088" in author_ids
        assert "A2028025389" in author_ids

    def test_paper_id_is_correct(self) -> None:
        result = extract_authorships(_work(0))
        assert all(a.paper_id == "W2741809807" for a in result)

    def test_empty_authorships_returns_empty_list(self) -> None:
        work = _work(2)  # "Language Models are Few-Shot Learners" — no authors
        assert work["authorships"] == []
        assert extract_authorships(work) == []

    def test_raises_on_missing_work_id(self) -> None:
        with pytest.raises(ValueError, match="missing 'id'"):
            extract_authorships({"authorships": []})

    def test_skips_authorship_with_missing_author_id(self) -> None:
        raw_work = {
            "id": "https://openalex.org/W999",
            "authorships": [{"author": {"display_name": "No ID"}}],
        }
        assert extract_authorships(raw_work) == []

    def test_null_authorships_field_returns_empty_list(self) -> None:
        raw_work = {"id": "https://openalex.org/W999", "authorships": None}
        assert extract_authorships(raw_work) == []


# ---------------------------------------------------------------------------
# extract_citations
# ---------------------------------------------------------------------------


class TestExtractCitations:
    def test_extracts_citations(self) -> None:
        result = extract_citations(_work(0))
        assert len(result) == 2
        assert all(isinstance(c, Citation) for c in result)

    def test_source_paper_id_is_correct(self) -> None:
        result = extract_citations(_work(0))
        assert all(c.source_paper_id == "W2741809807" for c in result)

    def test_cited_paper_ids(self) -> None:
        result = extract_citations(_work(0))
        cited_ids = {c.cited_paper_id for c in result}
        assert "W1982574323" in cited_ids
        assert "W2011534025" in cited_ids

    def test_empty_referenced_works_returns_empty_list(self) -> None:
        work = _work(2)
        assert work["referenced_works"] == []
        assert extract_citations(work) == []

    def test_raises_on_missing_work_id(self) -> None:
        with pytest.raises(ValueError, match="missing 'id'"):
            extract_citations({"referenced_works": []})

    def test_null_referenced_works_returns_empty_list(self) -> None:
        raw_work = {"id": "https://openalex.org/W999", "referenced_works": None}
        assert extract_citations(raw_work) == []

    def test_bert_cites_attention(self) -> None:
        result = extract_citations(_work(1))
        assert len(result) == 1
        assert result[0].cited_paper_id == "W2741809807"
        assert result[0].source_paper_id == "W2950377191"


# ---------------------------------------------------------------------------
# extract_paper_topics
# ---------------------------------------------------------------------------


class TestExtractPaperTopics:
    def test_extracts_topics(self) -> None:
        result = extract_paper_topics(_work(0))
        assert len(result) == 1
        assert isinstance(result[0], PaperTopic)
        assert result[0].topic_id == "T10116"
        assert result[0].paper_id == "W2741809807"

    def test_multiple_topics(self) -> None:
        result = extract_paper_topics(_work(1))
        assert len(result) == 2
        topic_ids = {pt.topic_id for pt in result}
        assert "T10116" in topic_ids
        assert "T11832" in topic_ids

    def test_empty_topics_returns_empty_list(self) -> None:
        work = _work(2)
        assert work["topics"] == []
        assert extract_paper_topics(work) == []

    def test_raises_on_missing_work_id(self) -> None:
        with pytest.raises(ValueError, match="missing 'id'"):
            extract_paper_topics({"topics": []})

    def test_null_topics_returns_empty_list(self) -> None:
        raw_work = {"id": "https://openalex.org/W999", "topics": None}
        assert extract_paper_topics(raw_work) == []

    def test_skips_topic_with_missing_id(self) -> None:
        raw_work = {
            "id": "https://openalex.org/W999",
            "topics": [{"display_name": "No ID"}],
        }
        assert extract_paper_topics(raw_work) == []


# ---------------------------------------------------------------------------
# extract_affiliations
# ---------------------------------------------------------------------------


class TestExtractAffiliations:
    def test_extracts_affiliations(self) -> None:
        result = extract_affiliations(_work(0))
        assert len(result) == 2
        assert all(isinstance(a, Affiliation) for a in result)

    def test_institution_ids(self) -> None:
        result = extract_affiliations(_work(0))
        assert all(a.institution_id == "I1289349794" for a in result)

    def test_author_ids_match_authorships(self) -> None:
        result = extract_affiliations(_work(0))
        author_ids = {a.author_id for a in result}
        assert "A2109829088" in author_ids
        assert "A2028025389" in author_ids

    def test_temporal_fields_are_none(self) -> None:
        result = extract_affiliations(_work(0))
        for affil in result:
            assert affil.start_year is None
            assert affil.end_year is None

    def test_primary_flag_set_for_first_author_first_institution(self) -> None:
        raw_work = {
            "id": "https://openalex.org/W999",
            "authorships": [
                {
                    "author": {"id": "https://openalex.org/A1", "display_name": "First"},
                    "author_position": "first",
                    "institutions": [
                        {"id": "https://openalex.org/I1", "display_name": "Inst A"},
                        {"id": "https://openalex.org/I2", "display_name": "Inst B"},
                    ],
                },
                {
                    "author": {"id": "https://openalex.org/A2", "display_name": "Second"},
                    "author_position": "last",
                    "institutions": [
                        {"id": "https://openalex.org/I1", "display_name": "Inst A"},
                    ],
                },
            ],
        }
        result = extract_affiliations(raw_work)
        primary = [a for a in result if a.primary]
        non_primary = [a for a in result if not a.primary]

        assert len(primary) == 1
        assert primary[0].author_id == "A1"
        assert primary[0].institution_id == "I1"
        assert len(non_primary) == 2

    def test_empty_authorships_returns_empty_list(self) -> None:
        work = _work(2)
        assert extract_affiliations(work) == []

    def test_null_authorships_returns_empty_list(self) -> None:
        raw_work = {"id": "https://openalex.org/W999", "authorships": None}
        assert extract_affiliations(raw_work) == []

    def test_skips_authorship_with_no_author_id(self) -> None:
        raw_work = {
            "id": "https://openalex.org/W999",
            "authorships": [
                {
                    "author": {},
                    "author_position": "first",
                    "institutions": [{"id": "https://openalex.org/I1"}],
                }
            ],
        }
        assert extract_affiliations(raw_work) == []

    def test_skips_institution_with_no_id(self) -> None:
        raw_work = {
            "id": "https://openalex.org/W999",
            "authorships": [
                {
                    "author": {"id": "https://openalex.org/A1"},
                    "author_position": "first",
                    "institutions": [{"display_name": "No ID"}],
                }
            ],
        }
        assert extract_affiliations(raw_work) == []

    def test_author_with_no_institutions_produces_no_affiliations(self) -> None:
        raw_work = {
            "id": "https://openalex.org/W999",
            "authorships": [
                {
                    "author": {"id": "https://openalex.org/A1"},
                    "author_position": "first",
                    "institutions": [],
                }
            ],
        }
        assert extract_affiliations(raw_work) == []
