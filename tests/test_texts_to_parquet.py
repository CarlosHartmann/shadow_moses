import json
import sys
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq
import zstandard

SCRIPT_DIR = (
    Path(__file__).resolve().parents[1] / "0-assets" / "raw_data" / "main" / "08-full-contexts" / "programmatic"
)
sys.path.insert(0, str(SCRIPT_DIR))

import texts_to_parquet as tp  # noqa: E402

LINES = [{"id": "c1", "author": "a", "body": "one", "created_utc": "1262304000", "parent_id": "t3_x",
          "link_id": "t3_x", "score": 3, "subreddit": "test"},
         {"id": "c2", "author": "b", "body": "zwei – ü", "created_utc": 1262304001, "parent_id": "t1_c1",
          "link_id": "t3_x", "score": 1, "subreddit": "test", "author_flair_text": "M"}]


def write_plain(path: Path, lines=LINES) -> None:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def write_compressed(path: Path, lines=LINES) -> None:
    text = "".join(json.dumps(line) + "\n" for line in lines).encode("utf-8")
    path.write_bytes(zstandard.ZstdCompressor().compress(text))


class TextsToParquetTests(unittest.TestCase):
    def test_plain_and_compressed_sources_give_the_same_table(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            write_plain(folder / "RC_2010-01.ndjson")
            write_compressed(folder / "RC_2010-02.ndjson.zst")
            counts = [tp.convert(folder / "RC_2010-01.ndjson", folder / "plain.parquet"),
                      tp.convert(folder / "RC_2010-02.ndjson.zst", folder / "compressed.parquet")]
            plain, compressed = pq.read_table(folder / "plain.parquet"), pq.read_table(folder / "compressed.parquet")
        self.assertEqual(counts, [2, 2])
        self.assertTrue(plain.equals(compressed))
        self.assertEqual(plain.column("body").to_pylist(), ["one", "zwei – ü"])

    def test_one_source_per_dump_file_and_the_uncompressed_one_wins(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            write_plain(folder / "RC_2010-01.ndjson")
            write_compressed(folder / "RC_2010-02.ndjson.zst")
            write_plain(folder / "RC_2010-03.ndjson")            # both forms of one month
            write_compressed(folder / "RC_2010-03.ndjson.zst")
            write_compressed(folder / "RC_2010-04.ndjson.zst.tmp")  # a compression under way
            names = [source.name for source in tp.dump_files(folder)]
            only_march = [source.name for source in tp.dump_files(folder, "RC_2010-03")]
        self.assertEqual(names, ["RC_2010-01.ndjson", "RC_2010-02.ndjson.zst", "RC_2010-03.ndjson"])
        self.assertEqual(only_march, ["RC_2010-03.ndjson"])


if __name__ == "__main__":
    unittest.main()
