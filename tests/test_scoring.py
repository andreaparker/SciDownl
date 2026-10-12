"""Focused scoring regression tests using invented bibliographic metadata."""

import unittest

from scidownl_reconcile.decisions import reconcile
from scidownl_reconcile.normalize import author_similarity, journal_similarity, normalize_text


def paper(**updates):
    record = {"record_id": "example", "title": "Measurement of fictional lattice oscillations",
              "authors": ["Ada Example"], "year": 1950, "journal": "Physical Review",
              "type": "article", "identifiers": {"doi": "10.9999/example"}}
    record.update(updates)
    return record


class ScoringTests(unittest.TestCase):
    def test_exact_metadata_verifies(self):
        result = reconcile(paper(identifiers={}), [paper()])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["selected"]["identifiers"]["doi"], "10.9999/example")

    def test_exact_identifier_with_missing_title_verifies(self):
        result = reconcile(paper(title="", authors=[], year=None, journal=""), [paper()])
        self.assertEqual(result["status"], "verified")

    def test_title_only_is_reviewed(self):
        target = {"title": "Measurement of fictional lattice oscillations"}
        result = reconcile(target, [paper()])
        self.assertEqual(result["status"], "plausible")
        self.assertLessEqual(result["ranked_candidates"][0]["confidence"], 0.74)

    def test_missing_candidate_author_blocks_verification(self):
        result = reconcile(paper(identifiers={}), [paper(authors=[])])
        self.assertNotEqual(result["status"], "verified")

    def test_wrong_author_is_rejected_even_with_exact_doi(self):
        result = reconcile(paper(), [paper(authors=["Alan Example"])])
        self.assertEqual(result["status"], "unresolved")
        self.assertFalse(result["ranked_candidates"][0]["eligible"])

    def test_year_shift_requires_review(self):
        result = reconcile(paper(identifiers={}), [paper(year=1951)])
        self.assertEqual(result["status"], "plausible")

    def test_year_conflict_rejects_article(self):
        result = reconcile(paper(identifiers={}), [paper(year=1958)])
        self.assertEqual(result["status"], "unresolved")

    def test_doi_conflict_is_rejected(self):
        result = reconcile(paper(), [paper(identifiers={"doi": "10.9999/wrong"})])
        self.assertEqual(result["status"], "unresolved")

    def test_abbreviated_journal(self):
        self.assertGreater(journal_similarity("Phys. Rev.", "Physical Review"), 0.9)
        result = reconcile(paper(journal="Phys. Rev.", identifiers={}), [paper()])
        self.assertEqual(result["status"], "verified")

    def test_initials_are_compatible_but_full_names_conflict(self):
        self.assertGreater(author_similarity("A. Example", "Ada Example"), 0.9)
        self.assertEqual(author_similarity("Albert Example", "Alfred Example"), 0.0)

    def test_cyrillic_and_accents(self):
        self.assertEqual(normalize_text("Иван Петров"), "ivan petrov")
        self.assertEqual(author_similarity("Émile Exemple", "E. Exemple"), 0.92)

    def test_explicit_translated_title_alias(self):
        target = paper(title="Measurement of fictional lattice oscillations", identifiers={},
                       title_variants=["Mesure des oscillations du réseau fictif"])
        candidate = paper(title="Mesure des oscillations du réseau fictif")
        result = reconcile(target, [candidate])
        self.assertEqual(result["status"], "verified")
        evidence = result["selected"]["components"]["title"]
        self.assertEqual(evidence["requested_variant"], target["title_variants"][0])
        self.assertEqual(evidence["normalized_requested"], evidence["normalized_observed"])

    def test_translation_is_not_inferred(self):
        candidate = paper(title="Mesure des oscillations du réseau fictif")
        self.assertEqual(reconcile(paper(identifiers={}), [candidate])["status"], "unresolved")

    def test_same_doi_merges_sources(self):
        left = paper(record_id="crossref", sources=[{"service": "crossref"}])
        right = paper(record_id="openalex", sources=[{"service": "openalex"}])
        result = reconcile(paper(identifiers={}), [left, right])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(len(result["ranked_candidates"]), 1)
        self.assertEqual(len(result["selected"]["sources"]), 2)

    def test_same_doi_provider_conflict_requires_review(self):
        result = reconcile(paper(identifiers={}), [paper(), paper(year=1952)])
        self.assertEqual(result["status"], "plausible")
        self.assertTrue(result["ranked_candidates"][0]["provider_conflicts"])

    def test_two_editions_remain_ambiguous(self):
        target = paper(type="book", identifiers={}, journal="", year=None)
        old = paper(type="book", identifiers={"isbn": "9780000000001"}, edition="1", year=1950)
        new = paper(type="book", identifiers={"isbn": "9780000000002"}, edition="2", year=1960)
        self.assertEqual(reconcile(target, [old, new])["status"], "ambiguous_editions")

    def test_explicit_edition_selects_only_requested_edition(self):
        target = paper(type="book", identifiers={}, edition="2")
        old = paper(type="book", identifiers={"isbn": "9780000000001"}, edition="1")
        new = paper(type="book", identifiers={"isbn": "9780000000002"}, edition="2")
        result = reconcile(target, [old, new])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["selected"]["edition"], "2")

    def test_tied_distinct_records_require_review(self):
        other = paper(record_id="other", identifiers={"doi": "10.9999/other"})
        result = reconcile(paper(identifiers={}), [paper(), other])
        self.assertEqual(result["status"], "plausible")

    def test_page_type_conflict_rejects(self):
        result = reconcile(paper(identifiers={}), [paper(type="book")])
        self.assertEqual(result["status"], "unresolved")

    def test_greek_particle_types_are_preserved(self):
        target = paper(title="Measurement of α particle emissions", identifiers={})
        candidate = paper(title="Measurement of β particle emissions")
        self.assertEqual(reconcile(target, [candidate])["status"], "unresolved")
        self.assertEqual(normalize_text("α particle"), "alpha particle")

    def test_missing_part_number_rejects(self):
        target = paper(title="Oscillations of a lattice part 1", identifiers={})
        candidate = paper(title="Oscillations of a lattice part")
        self.assertEqual(reconcile(target, [candidate])["status"], "unresolved")

    def test_conflicting_pages_rejects(self):
        result = reconcile(paper(pages="120-130", identifiers={}), [paper(pages="140-150")])
        self.assertEqual(result["status"], "unresolved")

    def test_requested_language_distinguishes_book_editions(self):
        target = paper(type="book", language="en", identifiers={})
        candidate = paper(type="book", language="de")
        self.assertEqual(reconcile(target, [candidate])["status"], "ambiguous_editions")

    def test_surname_particles_are_part_of_the_surname(self):
        self.assertEqual(author_similarity("Louis de Exemple", "de Exemple, Louis"), 1.0)
        self.assertEqual(author_similarity("Louis de Exemple", "Louis Exemple"), 0.0)

    def test_long_title_small_word_change_never_auto_verifies(self):
        title = "Detailed measurement of hydrogen loading in a fictional palladium lattice under controlled conditions"
        target = paper(title=title, identifiers={})
        candidate = paper(title=title.replace("hydrogen", "deuterium"))
        self.assertEqual(reconcile(target, [candidate])["status"], "plausible")

    def test_exact_identifier_cannot_override_fuzzy_title(self):
        title = "Detailed measurement of hydrogen loading in a fictional palladium lattice under controlled conditions"
        target = paper(title=title)
        candidate = paper(title=title.replace("hydrogen", "deuterium"))
        self.assertEqual(reconcile(target, [candidate])["status"], "plausible")

    def test_doi_only_target_accepts_numbered_candidate_title(self):
        target = {"identifiers": {"doi": "10.9999/example"}}
        candidate = paper(title="Measurement of a fictional oscillator Part 1")
        self.assertEqual(reconcile(target, [candidate])["status"], "verified")

    def test_distinct_requested_authors_require_distinct_observed_authors(self):
        target = paper(authors=["A. Vale", "A. M. Vale"], identifiers={})
        candidate = paper(authors=["Ada Marie Vale"])
        self.assertEqual(reconcile(target, [candidate])["status"], "unresolved")

    def test_assignment_can_move_ambiguous_initials_to_another_author(self):
        target = paper(authors=["A. Vale", "Ada Vale"], identifiers={})
        candidate = paper(authors=["Ada Vale", "Albert Vale"])
        self.assertEqual(reconcile(target, [candidate])["status"], "verified")

    def test_provider_pages_disagreement_requires_review(self):
        result = reconcile(paper(identifiers={}), [paper(pages="120-130"), paper(pages="140-150")])
        self.assertEqual(result["status"], "plausible")

    def test_provider_language_disagreement_requires_review(self):
        result = reconcile(paper(identifiers={}), [paper(language="en"), paper(language="de")])
        self.assertEqual(result["status"], "plausible")

    def test_provider_compatible_language_type_and_journal_merge(self):
        left = paper(language="en", type="article", journal="Phys. Rev. A")
        right = paper(language="English", type="journal-article", journal="Physical Review A")
        target = paper(identifiers={}, journal="Phys. Rev. A")
        self.assertEqual(reconcile(target, [left, right])["status"], "verified")

    def test_crossref_formatting_preserves_symbols_and_numbers(self):
        title = "Emission of α particles from H2O with intensity less than 3"
        markup = "Emission of &alpha; <i>particles</i> from H<sub>2</sub>O with intensity less than 3"
        self.assertEqual(normalize_text(title), normalize_text(markup))
        self.assertEqual(normalize_text("x < 3 and y > 2"), "x 3 and y 2")
        result = reconcile(paper(title=title, identifiers={}), [paper(title=markup)])
        self.assertEqual(result["status"], "verified")

    def test_journal_series_mismatch_rejects(self):
        target = paper(identifiers={}, journal="Physical Review A")
        candidate = paper(journal="Physical Review B")
        self.assertEqual(reconcile(target, [candidate])["status"], "unresolved")


if __name__ == "__main__":
    unittest.main()
