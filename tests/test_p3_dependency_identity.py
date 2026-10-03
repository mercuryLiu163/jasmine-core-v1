import tempfile
import unittest
from pathlib import Path
from jasmine_core.adapter.dependency_identity import directory_identity

class DependencyIdentityTests(unittest.TestCase):
    def test_membership_and_raw_bytes_are_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();(root/'module.js').write_bytes(b'hello\n')
            first=directory_identity(root);self.assertEqual(first['members'],1)
            (root/'module.js').write_bytes(b'hello')
            self.assertNotEqual(first['tree_sha256'],directory_identity(root)['tree_sha256'])
            with self.assertRaises(ValueError):directory_identity(root,max_bytes=1)
            with self.assertRaises(ValueError):directory_identity(root,max_members=0)
            (root/'alias').symlink_to(root/'module.js')
            with self.assertRaises(ValueError):directory_identity(root)
