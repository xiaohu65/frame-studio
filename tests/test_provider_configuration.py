"""Local endpoint/pricing checks; no real credential or network is used."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from backend.provider import WanClient, ProviderError, inspect_configuration, parse_workspace_base_url, _api_base


class ProviderConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'DASHSCOPE_API_KEY': '', 'BAILIAN_API_KEY': '', 'DASHSCOPE_WORKSPACE_ID': '', 'DASHSCOPE_HTTP_BASE_URL': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_legacy_key_presence_does_not_verify_workspace_or_authentication(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'DASHSCOPE_API_KEY': '', 'BAILIAN_API_KEY': '', 'DASHSCOPE_WORKSPACE_ID': '', 'DASHSCOPE_HTTP_BASE_URL': ''}):
            client = WanClient({'api_key': 'fake-offline-key', 'base_url': 'https://dashscope.aliyuncs.com/api/v1'}, Path(root))
            client.opener = Mock()
            client.opener.open.side_effect = AssertionError('network forbidden')
            self.assertTrue(client.configured)
            self.assertFalse(client.configuration_status()['ready'])
            self.assertFalse(client.configuration_status()['authentication_verified'])
            with self.assertRaisesRegex(ProviderError, '业务空间'):
                client._request_json('POST', client.base + '/services/aigc/video-generation/video-synthesis', {})
            with self.assertRaisesRegex(ProviderError, '业务空间'):
                client._upload(Path(root) / 'never-opened.png')
            client.opener.open.assert_not_called()

    def test_workspace_endpoint_region_and_explicit_workspace_must_agree(self):
        for region in ['cn-beijing', 'ap-southeast-1', 'ap-northeast-1', 'eu-central-1', 'us-east-1', 'cn-hongkong']:
            config = {'base_url': f'https://offline-workspace.{region}.maas.aliyuncs.com/api/v1'}
            report = inspect_configuration(config)
            self.assertTrue(report['ready'])
            self.assertEqual(report['region'], region)
            self.assertFalse(report['authentication_verified'])
            self.assertFalse(inspect_configuration({**config, 'workspace_id': 'another-workspace'})['ready'])
            if region != 'cn-beijing':
                self.assertFalse(inspect_configuration({**config, 'region': 'cn-beijing'})['ready'])

    def test_pricing_is_bound_to_endpoint_region_without_network(self):
        with tempfile.TemporaryDirectory() as root:
            for region, rate in [('cn-beijing', .6), ('ap-southeast-1', .74942), ('ap-northeast-1', .6)]:
                client = WanClient({'base_url': f'https://offline-workspace.{region}.maas.aliyuncs.com/api/v1'}, Path(root) / region)
                with patch.object(client, '_inspect_media', return_value=[{'type': 'reference_video', 'duration_seconds': 250 * 1001 / 24000}]):
                    report = client.preflight('offline prompt', [], {'resolution': '720P', 'duration': -1})
                self.assertEqual(report['estimate']['region'], region)
                self.assertEqual(report['estimate']['unit_yuan_per_second'], rate)
                self.assertEqual(report['estimate']['reserved_yuan'], round(31 * rate, 4))

    def test_console_base_url_extracts_space_and_region_without_changing_the_input(self):
        for region in ['cn-beijing', 'ap-southeast-1', 'ap-northeast-1', 'eu-central-1', 'us-east-1', 'cn-hongkong']:
            for path in ['', '/api/v1', '/compatible-mode/v1/']:
                value = f'https://offline-workspace.{region}.maas.aliyuncs.com{path}'
                config = {'base_url': value, 'workspace_id': ''}
                result = parse_workspace_base_url(value)
                self.assertEqual(result['workspace_id'], 'offline-workspace')
                self.assertEqual(result['region'], region)
                self.assertEqual(_api_base(config), f'https://offline-workspace.{region}.maas.aliyuncs.com/api/v1')
                report = inspect_configuration(config)
                self.assertTrue(report['ready'])
                self.assertEqual(report['workspace_id'], 'offline-workspace')
                self.assertEqual(config, {'base_url': value, 'workspace_id': ''}, 'normalization must not mutate caller configuration')

    def test_numeric_workspace_remains_eligible_and_conflicts_explain_key_record_id(self):
        report = inspect_configuration({'workspace_id': '1234567'})
        self.assertTrue(report['ready'])
        self.assertEqual(report['workspace_id'], '1234567')
        self.assertIn('密钥记录编号', report['workspace_help'])
        self.assertIn('无需重新输入', report['workspace_help'])
        mismatch = inspect_configuration({'base_url': 'https://offline-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1', 'workspace_id': '1234567'})
        self.assertFalse(mismatch['ready'])
        self.assertIn('API Key 记录编号', mismatch['issues'][0]['message'])
        self.assertFalse(report['authentication_verified'])

    def test_parser_refuses_spoofed_hosts_credentials_ports_queries_and_non_base_paths(self):
        native = 'https://offline-workspace.cn-beijing.maas.aliyuncs.com'
        invalid = [native + '.evil.example/api/v1', native.replace('https:', 'http:') + '/api/v1',
                   native.replace('https://', 'https://user:password@') + '/api/v1', native + ':8443/api/v1',
                   native + ':bad/api/v1', native + '/api/v1?key=not-a-real-key', native + '/api/v1#fragment',
                   native + '/api/v1/tasks/id', native + '/compatible-mode/v1/chat/completions',
                   'https://dashscope.aliyuncs.com/compatible-mode/v1']
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ProviderError):
                parse_workspace_base_url(value)

    def test_config_save_accepts_console_url_and_preserves_the_existing_key_offline(self):
        import json
        from backend import workflow
        with tempfile.TemporaryDirectory() as root, patch.object(workflow, 'CONFIG', Path(root) / 'private-config.json'), patch.object(workflow, 'RUNTIME_KEY', None):
            original = {'api_key': 'fake-existing-offline-key', 'workspace_id': '', 'base_url': 'https://dashscope.aliyuncs.com/api/v1'}
            workflow.CONFIG.write_text(json.dumps(original))
            result = workflow.set_config({'base_url': 'https://offline-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1', 'workspace_id': ''})
            saved = json.loads(workflow.CONFIG.read_text())
            self.assertEqual(saved['api_key'], original['api_key'])
            self.assertTrue(result['configured'])
            effective = inspect_configuration(saved)
            self.assertEqual(effective['workspace_id'], 'offline-workspace')
            self.assertEqual(effective['base_url'], 'https://offline-workspace.cn-beijing.maas.aliyuncs.com/api/v1')
            self.assertNotIn('api_key', effective)


if __name__ == '__main__':
    unittest.main()
