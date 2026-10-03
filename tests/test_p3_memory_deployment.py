import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from jasmine_core.context.memory_provider import MemoryProvider
from jasmine_core.errors import InvalidRequest

class MemoryDeploymentTests(unittest.TestCase):
    def test_private_worker_alias_and_pythonpath(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name).resolve();work=root/'work';work.mkdir();worker=root/'worker.py';worker.write_text('')
            config=root/'memory.json';alias=root/'alias.py';target=work/'model.py';target.write_text('');alias.symlink_to(target)
            value={'argv':[sys.executable,str(worker)],'provider_id':'fixed','global_bank':False,'environment':{}}
            def write():config.write_text(json.dumps(value));config.chmod(0o600)
            with patch.dict(os.environ,{'JASMINE_CORE_MEMORY_CONFIG':str(config),'JASMINE_CORE_WORKSPACE_ROOT':str(work)}):
                write();self.assertEqual(MemoryProvider.from_deployment().provider_id,'fixed')
                value['argv'][1]=str(alias);write()
                with self.assertRaises(InvalidRequest):MemoryProvider.from_deployment()
                inside_alias=work/'alias.py';inside_alias.symlink_to(worker)
                value['argv'][1]=str(inside_alias);write()
                with self.assertRaises(InvalidRequest):MemoryProvider.from_deployment()
                value['argv'][1]=str(worker)
                for path in ('',str(work),'.'):
                    value['environment']={'PYTHONPATH':path};write()
                    with self.assertRaises(InvalidRequest):MemoryProvider.from_deployment()
