import sqlite3
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import stage_itunes_revisions as native


def library(gid=0x8000000000000001):
    track = bytearray(0x1f4)
    track[:4] = b'mith'
    struct.pack_into('<IIII', track, 4, len(track), len(track), 0, 7)
    struct.pack_into('<Q', track, 0x1e4, gid)
    sub = b'mlth' + struct.pack('<II', 12, 1) + track
    return bytearray(b'msdh' + struct.pack('<III', 16, 16 + len(sub), 1) + sub)


class RevisionsTest(unittest.TestCase):
    def test_signed_sqlite_ids_and_no_input_mutation(self):
        original = library()
        gid = 0x8000000000000001
        before = bytes(original)
        result, count = native.stage(original, {gid: b'metadata'}, {gid: b'edges'})
        self.assertEqual(count, 1)
        self.assertEqual(bytes(original), before)
        self.assertEqual(struct.unpack_from('<I', result, 28 + 0x1f0)[0],
                         zlib.crc32(b'metadataedges') or 1)
        self.assertEqual(result[:28 + 0x1f0], original[:28 + 0x1f0])

    def test_missing_graph_fails_without_mutating_input(self):
        original = library()
        before = bytes(original)
        with self.assertRaises(ValueError):
            native.stage(original, {}, {})
        self.assertEqual(bytes(original), before)

    def test_loads_signed_sqlite_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'graph.sqlite'
            with closing(sqlite3.connect(path)) as db:
                for name in ('genius_metadata', 'genius_similarities'):
                    db.execute('CREATE TABLE %s (genius_id INTEGER, data BLOB)' % name)
                    db.execute('INSERT INTO %s VALUES (?, ?)' % name, (-9223372036854775807, b'data'))
                db.commit()
            metadata, similarities = native.read_graph(path)
            self.assertEqual(metadata[0x8000000000000001], b'data')
            self.assertEqual(similarities[0x8000000000000001], b'data')

    @unittest.skipUnless(sys.platform == 'win32', 'requires Windows CNG')
    def test_cng_aes_round_trip(self):
        block = bytes(range(32))
        self.assertEqual(native.aes_ecb(native.aes_ecb(block), decrypt=True), block)


if __name__ == '__main__':
    unittest.main()
