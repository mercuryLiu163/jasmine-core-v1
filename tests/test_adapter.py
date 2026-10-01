"""Protected adapter protocol components; no native lifecycle claim."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from jasmine_core import errors,ids
from jasmine_core.adapter.config import private_json
from jasmine_core.adapter.protocol import event_id
from jasmine_core.models import NewEvent


class ProtectedFiles(unittest.TestCase):
    def test_exact_raw_bytes_and_strict_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary).resolve()/'receipt.json'
            raw='{"text":"中文", "n":1}\n'.encode()
            path.write_bytes(raw);path.chmod(0o600)
            value,digest=private_json(path)
            self.assertEqual(value['text'],'中文');self.assertEqual(digest,hashlib.sha256(raw).hexdigest())
            for bad in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":1e999}',b'{"a":"\\ud800"}'):
                path.write_bytes(bad)
                with self.assertRaises(errors.InvalidRequest):private_json(path)
    def test_fifo_symlink_and_public_mode_fail_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();fifo=root/'fifo';os.mkfifo(fifo,0o600)
            with self.assertRaises(errors.InvalidRequest):private_json(fifo)
            path=root/'file';path.write_text('{}');path.chmod(0o644)
            with self.assertRaises(errors.InvalidRequest):private_json(path)
            path.chmod(0o600);alias=root/'alias';alias.symlink_to(path)
            with self.assertRaises(errors.InvalidRequest):private_json(alias)


class ProtocolIdentity(unittest.TestCase):
    def test_opaque_event_encoding_and_call_uniqueness(self):
        ident=event_id('native-call','thread','turn','call')
        self.assertTrue(ids.is_id(ident,'evt'));self.assertEqual(len(ident),30)
        self.assertEqual(ident,event_id('native-call','thread','turn','call'))
        self.assertNotEqual(ident,event_id('reserve-key','thread','turn','call'))
    def test_public_raw_cannot_poison_adapter_command_namespace(self):
        base={'event_type':'user.prompt','source_system':'ordinary','host_id':ids.new_id('hst'),
              'payload':{'text':'raw'}}
        for event_type,source in [('adapter.operation_reserved','ordinary'),('user.prompt','core-native-adapter')]:
            with self.assertRaises(errors.InvalidRequest):NewEvent.from_request({**base,'event_type':event_type,'source_system':source},
                actor_id=ids.new_id('act'),actor_kind='system')
