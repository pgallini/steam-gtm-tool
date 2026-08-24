import unittest
from unittest.mock import Mock, patch

from supabase.steam_sync.catalog_client import (
    SteamCatalogClient,
    SteamCatalogError,
    SteamRateLimitError,
)


class SteamCatalogClientTests(unittest.TestCase):
    def test_base_url_uses_partner_endpoint(self):
        self.assertEqual(
            SteamCatalogClient.BASE_URL,
            'https://partner.steam-api.com/IStoreService/GetAppList/v1/',
        )

    @patch('requests.Session.get')
    def test_http_failure_redacts_api_key_and_includes_status(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.reason = 'Forbidden'
        mock_response.url = (
            'https://partner.steam-api.com/IStoreService/GetAppList/v1/'
            '?key=super-secret-key&input_json={"foo":"bar"}'
        )
        mock_response.text = 'Request denied: invalid API key'
        mock_get.return_value = mock_response

        client = SteamCatalogClient(api_key='super-secret-key', timeout=5, max_retries=1)

        with self.assertRaises(SteamCatalogError) as ctx:
            client._request({'key': 'super-secret-key', 'input_json': '{"foo":"bar"}'})

        message = str(ctx.exception)
        self.assertIn('HTTP 403 Forbidden', message)
        self.assertIn('https://partner.steam-api.com/IStoreService/GetAppList/v1/', message)
        self.assertIn('[REDACTED]', message)
        self.assertNotIn('super-secret-key', message)
        self.assertIn('Request denied: invalid API key', message)

    @patch('requests.Session.get')
    def test_http_429_raises_rate_limit_error(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.reason = 'Too Many Requests'
        mock_response.url = 'https://partner.steam-api.com/IStoreService/GetAppList/v1/?key=super-secret-key'
        mock_response.headers = {}
        mock_response.text = 'rate limited'
        mock_get.return_value = mock_response

        client = SteamCatalogClient(api_key='super-secret-key', timeout=5, max_retries=1)

        with self.assertRaises(SteamRateLimitError):
            client._request({'key': 'super-secret-key'})


if __name__ == '__main__':
    unittest.main()
