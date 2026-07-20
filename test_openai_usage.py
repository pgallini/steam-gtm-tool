import unittest
from types import SimpleNamespace

from supabase.openai_usage import response_token_usage


class OpenAIUsageTests(unittest.TestCase):
    def test_extracts_response_usage_and_details(self):
        response = SimpleNamespace(usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=30,
            total_tokens=150,
            input_tokens_details=SimpleNamespace(cached_tokens=40),
            output_tokens_details=SimpleNamespace(reasoning_tokens=12),
        ))

        usage = response_token_usage(
            response,
            model='test-model',
            operation='Test operation',
            candidate_count=3,
        )

        self.assertEqual(usage['input_tokens'], 120)
        self.assertEqual(usage['output_tokens'], 30)
        self.assertEqual(usage['total_tokens'], 150)
        self.assertEqual(usage['cached_input_tokens'], 40)
        self.assertEqual(usage['reasoning_tokens'], 12)
        self.assertEqual(usage['candidate_count'], 3)
        self.assertTrue(usage['usage_available'])

    def test_supports_legacy_completion_token_names(self):
        response = {'usage': {'prompt_tokens': 80, 'completion_tokens': 20, 'total_tokens': 100}}

        usage = response_token_usage(response, model='test-model', operation='Test operation')

        self.assertEqual(usage['input_tokens'], 80)
        self.assertEqual(usage['output_tokens'], 20)
        self.assertEqual(usage['total_tokens'], 100)
        self.assertTrue(usage['usage_available'])

    def test_handles_missing_usage_without_estimating(self):
        usage = response_token_usage({}, model='test-model', operation='Test operation')

        self.assertEqual(usage['input_tokens'], 0)
        self.assertEqual(usage['output_tokens'], 0)
        self.assertEqual(usage['total_tokens'], 0)
        self.assertNotIn('cached_input_tokens', usage)
        self.assertFalse(usage['usage_available'])


if __name__ == '__main__':
    unittest.main()
