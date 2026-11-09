"""Regression tests for the device PID / Genius ID statistics bridge."""
import sqlite3
import random
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from genius_sim import Ctx, SkipCount, load_db


class GeniusStatsTest(unittest.TestCase):
    def make_db(self, root, nano6):
        with closing(sqlite3.connect(root / 'Library.itdb')) as c:
            c.executescript('''
                create table item(pid integer, genius_id integer);
                insert into item values(900001, 12345);
                create table container(pid, name, distinguished_kind, smart_is_genius,
                    smart_is_dynamic, smart_is_filtered, smart_limit_kind, smart_criteria);
                create table item_to_container(item_pid, container_pid, physical_order);
            ''')
        with closing(sqlite3.connect(root / 'Genius.itdb')) as c:
            c.executescript('''
                create table genius_metadata(genius_id integer, data blob);
                create table genius_similarities(genius_id integer, data blob);
                create table genius_config(id, version, default_num_results, min_num_results, data);
            ''')
        with closing(sqlite3.connect(root / 'Dynamic.itdb')) as c:
            gid = ', genius_id integer' if nano6 else ''
            c.execute('create table item_stats(item_pid, date_played, play_count_user, '
                      'date_skipped, skip_count_user' + gid + ')')
            c.execute('insert into item_stats values (' + ','.join('?' * (6 if nano6 else 5)) + ')',
                      (900001, 0, 0, 9990, 5, 12345) if nano6 else (900001, 0, 0, 9990, 5))
            c.commit()

    def test_stats_are_indexed_by_genius_id_on_both_generations(self):
        for nano6 in (True, False):
            with self.subTest(nano6=nano6), tempfile.TemporaryDirectory() as tmp:
                self.make_db(Path(tmp), nano6)
                db = load_db(tmp)
                self.assertEqual(db['stats'][12345], (0, 0, 9990, 5))
                rule = SkipCount({'p': [2, 604800, 0, 50]}, None)
                self.assertEqual(rule.check(Ctx(db, random.Random(1), 10000), {}, {'gid': 12345}), 1)
                db['stats'][12345] = (0, 0, 9990, 2)
                self.assertEqual(rule.check(Ctx(db, random.Random(1), 10000), {}, {'gid': 12345}), 0)
                db['stats'][12345] = (0, 0, 10001, 5)
                self.assertEqual(rule.check(Ctx(db, random.Random(1), 10000), {}, {'gid': 12345}), 0)


if __name__ == '__main__':
    unittest.main()
