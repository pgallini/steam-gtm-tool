from __future__ import annotations

import unittest
from unittest.mock import patch

from supabase.review_pipeline import load_candidate_reviews, upsert_reviews
from supabase.research_pipeline import add_tag_discovery_results, buildCandidateUniverse, persist_scoring_batches, selected_classification_rows


class BuildCandidateUniverseTests(unittest.TestCase):
    @patch('supabase.research_pipeline.addRunEvent')
    @patch('supabase.research_pipeline.client')
    @patch('supabase.research_pipeline.classify_run')
    @patch('supabase.research_pipeline.score_run')
    @patch('supabase.research_pipeline.enrich_run')
    @patch('supabase.research_pipeline.prepareRunCandidates')
    @patch('supabase.research_pipeline.candidateSetIsApproved', return_value=False)
    def test_runs_candidate_stages_in_sequence(
        self,
        mock_approved,
        mock_prepare,
        mock_enrich,
        mock_score,
        mock_classify,
        mock_client,
        mock_add_event,
    ) -> None:
        calls = []
        mock_prepare.side_effect = lambda run_id: calls.append(('prepare', run_id)) or {'count': 1}
        mock_enrich.side_effect = lambda run_id: calls.append(('discover', run_id)) or {'enriched_app_count': 2, 'discovered_count': 2}
        mock_score.side_effect = lambda run_id: calls.append(('filter', run_id)) or {'scored_count': 2}
        mock_classify.side_effect = lambda rule_id, run_id: calls.append(('classify', rule_id, run_id)) or {'classified_count': 2}

        result = buildCandidateUniverse('run-1')

        self.assertEqual(
            calls,
            [
                ('prepare', 'run-1'),
                ('discover', 'run-1'),
                ('filter', 'run-1'),
                ('classify', 'rule_based_v1', 'run-1'),
            ],
        )
        self.assertEqual(result['status'], 'needs_review')
        mock_client.update.assert_called_once()
        mock_add_event.assert_any_call(
            'run-1',
            'classification',
            'candidate_universe_completed',
            'Build Candidate Universe completed; candidates are ready for review',
        )


class UpsertReviewsTests(unittest.TestCase):
    @patch('supabase.review_pipeline.client')
    def test_bulk_upserts_identified_reviews(self, mock_client) -> None:
        payloads = [
            {'steam_review_id': 'review-1', 'review_text': 'First'},
            {'steam_review_id': 'review-2', 'review_text': 'Second'},
        ]

        count = upsert_reviews(payloads)

        self.assertEqual(count, 2)
        mock_client.upsert_batches.assert_called_once_with(
            'steam_reviews',
            payloads,
            on_conflict='steam_review_id',
            batch_size=500,
            returning='minimal',
        )
        mock_client.insert.assert_not_called()


    @patch('supabase.review_pipeline.client')
    def test_inserts_reviews_without_ids_separately(self, mock_client) -> None:
        identified = {'steam_review_id': 'review-1', 'review_text': 'First'}
        unidentified = {'steam_review_id': None, 'review_text': 'Anonymous'}

        count = upsert_reviews([identified, unidentified])

        self.assertEqual(count, 2)
        mock_client.upsert_batches.assert_called_once_with(
            'steam_reviews',
            [identified],
            on_conflict='steam_review_id',
            batch_size=500,
            returning='minimal',
        )
        mock_client.insert.assert_called_once_with('steam_reviews', [unidentified], returning='minimal')

    @patch('supabase.review_pipeline.client')
    def test_empty_page_does_not_call_supabase(self, mock_client) -> None:
        self.assertEqual(upsert_reviews([]), 0)
        mock_client.upsert_batches.assert_not_called()
        mock_client.insert.assert_not_called()


class ReviewSamplingTests(unittest.TestCase):
    @patch('supabase.review_pipeline.client')
    def test_loads_positive_and_negative_reviews_separately(self, mock_client) -> None:
        mock_client.select.side_effect = [[{'steam_review_id': 'positive'}], [{'steam_review_id': 'negative'}]]

        positives, negatives = load_candidate_reviews(123, limit_per_sentiment=50)

        self.assertEqual(positives, [{'steam_review_id': 'positive'}])
        self.assertEqual(negatives, [{'steam_review_id': 'negative'}])
        self.assertEqual(mock_client.select.call_count, 2)
        mock_client.select.assert_any_call(
            'steam_reviews',
            '*',
            {'steam_appid': 'eq.123', 'order': 'fetched_at.desc', 'limit': '50', 'voted_up': 'eq.true'},
        )
        mock_client.select.assert_any_call(
            'steam_reviews',
            '*',
            {'steam_appid': 'eq.123', 'order': 'fetched_at.desc', 'limit': '50', 'voted_up': 'eq.false'},
        )


class SelectedClassificationRowsTests(unittest.TestCase):
    @patch('supabase.research_pipeline.client')
    def test_loads_only_selected_candidates_in_final_rank_order(self, mock_client) -> None:
        expected = [{'candidate_id': 'candidate-1'}]
        mock_client.select.return_value = expected

        rows = selected_classification_rows('run-1')

        self.assertEqual(rows, expected)
        mock_client.select.assert_called_once_with(
            'v_run_candidate_summary',
            '*',
            {
                'run_id': 'eq.run-1',
                'is_selected_for_report': 'eq.true',
                'order': 'final_rank.asc.nullslast',
            },
        )


class DiscoveryDeduplicationTests(unittest.TestCase):
    @patch('supabase.research_pipeline.add_discovered_candidate')
    @patch('supabase.research_pipeline.upsert_enriched_steam_app')
    @patch('supabase.research_pipeline.search_steam_by_tag_ids')
    def test_skips_seen_and_repeated_search_results(self, mock_search, mock_enrich, mock_add) -> None:
        mock_search.return_value = [
            {'appid': 10},
            {'appid': 20},
            {'appid': 20},
        ]
        mock_add.return_value = {'id': 'candidate-20'}
        seen_appids = {10}

        added = add_tag_discovery_results(
            {'id': 'run-1'},
            seen_appids=seen_appids,
            seed_tags=[{'name': 'Action', 'tagid': 19}],
            tag_names=['Action'],
            rank_offset=1,
            max_results=50,
            source='tag_search',
            country='us',
            language='english',
        )

        self.assertEqual(added, 1)
        self.assertEqual(seen_appids, {10, 20})
        mock_enrich.assert_called_once_with(20, country='us', language='english')
        mock_add.assert_called_once()


class ScoringBatchTests(unittest.TestCase):
    @patch('supabase.research_pipeline.client')
    def test_bulk_upserts_scores_and_candidate_updates(self, mock_client) -> None:
        scores = [{'candidate_id': 'candidate-1', 'scoring_version': 'v1'}]
        candidates = [{'id': 'candidate-1', 'pipeline_status': 'scored'}]

        persist_scoring_batches(scores, candidates)

        self.assertEqual(mock_client.upsert_batches.call_count, 2)
        mock_client.upsert_batches.assert_any_call(
            'candidate_scores',
            scores,
            on_conflict='candidate_id,scoring_version',
            batch_size=500,
            returning='minimal',
        )
        mock_client.upsert_batches.assert_any_call(
            'run_candidates',
            candidates,
            on_conflict='id',
            batch_size=500,
            returning='minimal',
        )

    @patch('supabase.research_pipeline.client')
    def test_empty_scoring_batches_do_not_call_supabase(self, mock_client) -> None:
        persist_scoring_batches([], [])
        mock_client.upsert_batches.assert_not_called()


if __name__ == '__main__':
    unittest.main()
