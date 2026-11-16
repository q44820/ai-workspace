import unittest
from scripts.check_publication import issues

class PublicationTests(unittest.TestCase):
    def test_docs_allowed(self):
        self.assertEqual(issues('README.md', '项目说明'.encode()), [])
    def test_env_rejected(self):
        self.assertTrue(issues('.env', b'hello'))
    def test_private_folder_rejected(self):
        self.assertTrue(issues('private/report.md', b'hello'))
    def test_key_rejected(self):
        self.assertTrue(issues('x.txt', b'-----BEGIN ' + b'PRIVATE KEY-----'))
    def test_token_rejected(self):
        self.assertTrue(issues('x.txt', ('ghp_' + 'A' * 30).encode()))
    def test_assignment_rejected(self):
        self.assertTrue(issues('x.txt', ('api_secret=' + 'X' * 20).encode()))
    def test_binary_rejected(self):
        self.assertTrue(issues('x.bin', b'abc\x00def'))

if __name__ == '__main__':
    unittest.main()
