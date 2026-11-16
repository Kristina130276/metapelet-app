import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import budget_guard as b

class PersistentBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbpath = Path(self.tmp.name) / 'budget.sqlite3'
        self.patcher = patch.object(b, 'DB_PATH', self.dbpath)
        self.patcher.start()
    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
    def test_counter_survives_new_connections_and_reload(self):
        self.assertTrue(b.try_consume_reply('synthetic', '127.0.0.1')[0])
        with patch.dict('os.environ', {'BUDGET_DB_PATH': str(self.dbpath)}):
            importlib.reload(b)
            self.assertEqual(b.status()['replies_used'], 1)
        b.DB_PATH = self.dbpath
    def test_concurrent_reservations_do_not_exceed_global_limit(self):
        with patch.object(b,'DAILY_GLOBAL_REPLIES',5):
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(lambda n:b.try_consume_reply(str(n),'127.0.0.1')[0],range(30)))
            self.assertEqual(sum(results),5)
            self.assertEqual(b.status()['replies_used'],5)
    def test_unavailable_database_blocks_paid_work(self):
        with patch.object(b,'DB_PATH',Path('/dev/null/blocked.sqlite3')):
            self.assertEqual(b.try_consume_reply('synthetic','ip'),(False,'storage_unavailable'))
            self.assertEqual(b.try_consume_tts('synthetic'),(False,'storage_unavailable'))
    def test_tts_permits_shared_between_connections_and_expire(self):
        b.allow_tts_for('Synthetic reply')
        for _ in range(b.TTS_PLAYS_PER_REPLY):
            self.assertTrue(b.try_consume_tts('Synthetic reply')[0])
        self.assertEqual(b.try_consume_tts('Synthetic reply'),(False,'unknown_text'))
        b.allow_tts_for('Another reply')
        with patch.object(b,'_today',return_value='2099-01-01'):
            self.assertEqual(b.try_consume_tts('Another reply'),(False,'unknown_text'))
