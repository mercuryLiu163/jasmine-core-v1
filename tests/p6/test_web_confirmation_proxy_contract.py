"""Item 6 opt-in allowlist contracts; does not assert Core/browser Gate acceptance."""
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]

class WebConfirmationProxyContract(unittest.TestCase):
    def test_exact_method_path_separation(self):
        contract = json.loads((ROOT / 'web/server/core-paths.json').read_text())
        reads = [re.compile(path) for path in contract['web_confirmation_read_paths']]
        writes = [re.compile(path) for path in contract['web_confirmation_write_paths']]
        preparation = '/v1/evidence/confirmations/evt_' + '0' * 26
        for path in ['/v1/auth/me', preparation]:
            self.assertTrue(any(pattern.fullmatch(path) for pattern in reads))
            self.assertFalse(any(pattern.fullmatch(path) for pattern in writes))
        for path in ['/v1/evidence/confirmations/prepare', '/v1/evidence/confirmations/submit']:
            self.assertTrue(any(pattern.fullmatch(path) for pattern in writes))
            self.assertFalse(any(pattern.fullmatch(path) for pattern in reads))
        for path in ['/v1/auth/keys', '/v1/audit', '/v1/events', '/v1/evidence/confirm', '/v1/tool-results', '/v1/workspaces/fingerprint', '/v1/evidence/confirmations/tsk_' + '0'*26]:
            self.assertFalse(any(pattern.fullmatch(path) for pattern in reads + writes), path)
        # New paths may only be appended via the opt-in sections.
        for path in ['/v1/auth/me', preparation, '/v1/evidence/confirmations/prepare', '/v1/evidence/confirmations/submit']:
            self.assertFalse(any(re.fullmatch(pattern, path) for pattern in contract['paths'] + contract['write_paths']))

    def test_both_hosts_share_flag_and_origin_refusal(self):
        for name in ['core-proxy.ts', 'serve.mjs']:
            source = (ROOT / 'web/server' / name).read_text()
            self.assertIn('P6_ENABLE_WEB_CONFIRMATIONS', source)
            self.assertIn('web_confirmation_read_paths', source)
            self.assertIn('web_confirmation_write_paths', source)
            self.assertIn('proxy_origin_not_allowed', source)

if __name__ == '__main__':
    unittest.main()
