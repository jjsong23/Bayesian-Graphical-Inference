import csv
import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from path_picker.client import ResponsesClient
from path_picker.database import RunDatabase
from path_picker.grounding import compact_evidence, quotation_excerpt
from path_picker.ingest import import_agent_graphs


class UsageTests(unittest.TestCase):
    def test_verifier_excerpt_contains_late_quote(self):
        quote = 'This experiment did not detect binding in these cells.'
        excerpt = quotation_excerpt('intro ' * 10000 + quote + ' conclusion ' * 1000, quote)
        self.assertIn(quote.lower(), excerpt)
        self.assertLessEqual(len(excerpt), 12000)

    def test_usage_limits_and_secret_free_log(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / 'usage.jsonl'
            client = ResponsesClient(base_url='https://api.openai.com/v1', api_key='secret-test', usage_log=log, max_total_calls=1)
            response = {'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 30, 'output_tokens_details': {'reasoning_tokens': 20}}, 'tool_usage': {'web_search': {'num_requests': 3}}, 'output': []}
            with patch('path_picker.client.urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
                client.request({'model': 'test-model'})
            records = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual(records[-1]['output_tokens'], 30)
            self.assertEqual(records[-1]['reasoning_tokens_included_in_output'], 20)
            self.assertEqual(records[-1]['bing_requests'], 3)
            self.assertNotIn('secret-test', log.read_text())
            resumed = ResponsesClient(base_url='https://api.openai.com/v1', api_key='secret-test', usage_log=log, max_total_calls=1)
            with self.assertRaisesRegex(RuntimeError, 'attempt limit'):
                resumed.request({'model': 'test-model'})

    def test_generated_urls_are_not_search_provenance(self):
        raw = {'output': [{'type': 'message', 'url': 'https://fake.org', 'content': [{'type': 'output_text', 'text': '{"url":"https://fake.org"}', 'annotations': []}]}, {'type': 'web_search_call', 'action': {'sources': [{'url': 'https://real.org'}]}}]}
        self.assertEqual(ResponsesClient.source_urls(raw), {'https://real.org'})

    def test_incomplete_and_missing_search_rejected(self):
        client = ResponsesClient(base_url='https://api.openai.com/v1', api_key='secret')
        with patch.object(client, 'request', return_value={'status': 'incomplete'}):
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                client.structured(model='test', prompt='test', schema_name='test', schema={})
        with patch.object(client, 'request', return_value={'status': 'completed', 'output': []}):
            with self.assertRaisesRegex(RuntimeError, 'web search'):
                client.structured(model='test', prompt='test', schema_name='test', schema={}, web_search=True, require_web_search=True)


class LegacyTests(unittest.TestCase):
    def test_all_returned_paths_and_missing_ledgers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (root / 'ranked_paths.tsv').open('w', newline='') as handle:
                fields = ['rank', 'start_symbol', 'target_symbol', 'path_symbols']
                writer = csv.DictWriter(handle, fields, delimiter='\t')
                writer.writeheader()
                writer.writerow(dict(zip(fields, ['1', 'A', 'B', 'A -> B'])))
                writer.writerow(dict(zip(fields, ['2', 'A', 'C', 'A -> B -> C'])))
            with gzip.open(root / 'all_returned_path_evidence_ledgers.json.gz', 'wt') as handle:
                json.dump([{'kind': 'node', 'symbol': 'A', 'streams': [{'stream_id': 'test', 'applied_bayes_factor': 3, 'observed': True}]}], handle)
            database = RunDatabase(root / 'test.sqlite3')
            inventory = import_agent_graphs(root / 'complete_session_full.html', database)
            self.assertEqual(inventory['paths'], 2)
            self.assertEqual(inventory['nodes'], 3)
            self.assertEqual(inventory['edges'], 2)
            self.assertEqual(len(inventory['missing_evidence_ledgers']), 4)
            with database.connect() as db:
                payload = json.loads(db.execute("SELECT payload_json FROM session_evidence WHERE item_id='A'").fetchone()[0])
            self.assertEqual(compact_evidence(payload)['evidence'][0]['applied_bf'], 3)

    def test_session_payloads_not_replaced_by_best_posterior(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for session, bf in [('one', 2), ('two', 10)]:
                directory = root / session
                directory.mkdir()
                with gzip.open(directory / 'agent_graph.json.gz', 'wt') as handle:
                    json.dump({'run_id': session, 'nodes': [{'id': 'A', 'posterior_probability': bf / 11, 'evidence_ledger': {'evidence': [{'applied_bf': bf}]}}]}, handle)
            database = RunDatabase(root / 'test.sqlite3')
            import_agent_graphs(root, database)
            with database.connect() as db:
                rows = db.execute("SELECT session_id,payload_json FROM session_evidence ORDER BY session_id").fetchall()
            self.assertEqual([json.loads(r[1])['evidence_ledger']['evidence'][0]['applied_bf'] for r in rows], [2, 10])
