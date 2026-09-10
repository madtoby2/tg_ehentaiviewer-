"""Whos.tv shared account pool: TLS-fingerprint client + pooled search."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrapers import whos_tv


def _mock_session():
    session = mock.Mock()
    session.headers = mock.Mock()
    return session


def _client(session):
    return whos_tv.WhosTvClient('u1', 'p1', session_factory=mock.Mock(return_value=session))


class WhosClientTransportTests(unittest.TestCase):
    def test_client_uses_chrome_impersonation_session(self):
        factory = mock.Mock(return_value=_mock_session())
        whos_tv.WhosTvClient('u1', 'p1', session_factory=factory)
        factory.assert_called_once_with(impersonate='chrome')

    def test_upload_uses_curl_multipart_form(self):
        session = _mock_session()
        response = mock.Mock(text='https://whos.tv/search-wait/abc')
        response.raise_for_status = mock.Mock()
        session.post.return_value = response
        client = _client(session)
        with tempfile.NamedTemporaryFile(suffix='.jpg') as image:
            image.write(b'jpeg-data')
            image.flush()
            result = client._upload(image.name)
        self.assertIn('multipart', session.post.call_args.kwargs)
        self.assertNotIn('files', session.post.call_args.kwargs)
        self.assertEqual(result, 'https://whos.tv/search-wait/abc')

    def test_can_search_reads_points_endpoint(self):
        session = _mock_session()
        response = mock.Mock()
        response.raise_for_status = mock.Mock()
        response.json.return_value = {'code': 0, 'data': {'can_search': True, 'points_balance': 120}}
        session.get.return_value = response
        data = _client(session).can_search()
        self.assertTrue(data['can_search'])
        self.assertEqual(session.get.call_args.kwargs['params']['action'], 'search_screenshot')

    def test_profile_returns_data(self):
        session = _mock_session()
        response = mock.Mock()
        response.raise_for_status = mock.Mock()
        response.json.return_value = {'code': 0, 'data': {'points_balance': 55}}
        session.get.return_value = response
        self.assertEqual(_client(session).profile()['points_balance'], 55)


class AccountPoolTests(unittest.TestCase):
    def test_load_accounts_merges_primary_and_file_without_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'accounts.json')
            Path(path).write_text(json.dumps([
                {'username': 'pool1', 'password': 'p1'},
                {'username': 'MAIN', 'password': 'ignored'},
                {'username': 'pool2', 'password': 'p2'},
            ]), encoding='utf-8')
            accounts = whos_tv.load_accounts(path, 'main', 'secret')
        self.assertEqual([a['username'] for a in accounts], ['main', 'pool1', 'pool2'])
        self.assertEqual(accounts[0]['password'], 'secret')

    def test_load_accounts_missing_or_broken_file_is_empty(self):
        self.assertEqual(whos_tv.load_accounts('/tmp/does-not-exist-whos.json'), [])
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'accounts.json')
            Path(path).write_text('not-json', encoding='utf-8')
            self.assertEqual(whos_tv.load_accounts(path), [])

    def test_pool_skips_account_without_points(self):
        clients = []

        def factory(username, password):
            client = mock.Mock()
            if username == 'poor':
                client.can_search.return_value = {'can_search': False, 'points_balance': 0}
            else:
                client.can_search.return_value = {'can_search': True, 'points_balance': 240}
                client.search_authenticated.return_value = {
                    'result_url': 'x', 'matches': [{'code': 'ABP-123'}]}
            clients.append(client)
            return client

        pool = whos_tv.WhosAccountPool(
            [{'username': 'poor', 'password': 'p'}, {'username': 'rich', 'password': 'p'}],
            client_factory=factory)
        result = pool.search('/tmp/img.jpg')
        self.assertEqual(result['matches'][0]['code'], 'ABP-123')
        self.assertEqual(result['account_index'], 2)
        self.assertEqual(result['points_balance'], 240)
        clients[0].search_authenticated.assert_not_called()
        self.assertTrue(all(client.close.called for client in clients))

    def test_pool_falls_back_when_account_errors(self):
        def factory(username, password):
            client = mock.Mock()
            client.can_search.return_value = {'can_search': True, 'points_balance': 60}
            if username == 'broken':
                client.login.side_effect = RuntimeError('login failed')
            else:
                client.search_authenticated.return_value = {'matches': []}
            return client

        pool = whos_tv.WhosAccountPool(
            [{'username': 'broken', 'password': 'p'}, {'username': 'ok', 'password': 'p'}],
            client_factory=factory)
        self.assertEqual(pool.search('/tmp/img.jpg')['account_index'], 2)

    def test_pool_returns_none_when_no_account_can_search(self):
        def factory(username, password):
            client = mock.Mock()
            client.can_search.return_value = {'can_search': False, 'points_balance': 0}
            return client

        pool = whos_tv.WhosAccountPool([{'username': 'a', 'password': 'p'}], client_factory=factory)
        self.assertIsNone(pool.search('/tmp/img.jpg'))

    def test_pool_raises_when_every_account_errors(self):
        def factory(username, password):
            client = mock.Mock()
            client.login.side_effect = RuntimeError('boom')
            return client

        pool = whos_tv.WhosAccountPool(
            [{'username': 'a', 'password': 'p'}, {'username': 'b', 'password': 'p'}],
            client_factory=factory)
        with self.assertRaises(RuntimeError):
            pool.search('/tmp/img.jpg')


class SearchEntryPointTests(unittest.TestCase):
    def test_search_uses_pool_file_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'accounts.json')
            Path(path).write_text(json.dumps([{'username': 'pool1', 'password': 'p1'}]),
                                  encoding='utf-8')
            used = []

            def factory(username, password):
                used.append(username)
                client = mock.Mock()
                client.can_search.return_value = {'can_search': True, 'points_balance': 60}
                client.search_authenticated.return_value = {'matches': [{'code': 'X-1'}]}
                return client

            result = whos_tv.search('', '', '/tmp/img.jpg', accounts_file=path, client_factory=factory)
        self.assertEqual(used, ['pool1'])
        self.assertEqual(result['matches'][0]['code'], 'X-1')

    def test_search_uses_primary_account_without_pool_file(self):
        used = []

        def factory(username, password):
            used.append(username)
            client = mock.Mock()
            client.can_search.return_value = {'can_search': True, 'points_balance': 60}
            client.search_authenticated.return_value = {'matches': []}
            return client

        whos_tv.search('solo', 'pw', '/tmp/img.jpg', client_factory=factory)
        self.assertEqual(used, ['solo'])

    def test_search_returns_none_without_any_account(self):
        self.assertIsNone(whos_tv.search('', '', '/tmp/no.jpg'))

    def test_search_swallows_pool_failure(self):
        def factory(username, password):
            client = mock.Mock()
            client.login.side_effect = RuntimeError('boom')
            return client

        self.assertIsNone(whos_tv.search('u', 'p', '/tmp/img.jpg', client_factory=factory))


if __name__ == '__main__':
    unittest.main()
