"""The large files of the pipeline can be read as they are or from their .zst archives, and the
optional compression script replaces them safely."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb
import zstandard

MAIN = Path(__file__).resolve().parents[1] / "0-assets" / "raw_data" / "main"
for folder in ("shared", "optional", "02-filtering", "03a-grouping", "04-cross-subreddit-data",
               "06-baseline-reduction", "08-full-contexts/programmatic"):
    sys.path.insert(0, str(MAIN / folder))

import baseline_reduction  # noqa: E402
import compress_large_files as clf  # noqa: E402
import cross_subreddit  # noqa: E402
import filtering  # noqa: E402
import grouping  # noqa: E402
import large_files  # noqa: E402
import prepare_ids  # noqa: E402

COMMENTS = [{"id": f"c{n}", "author": f"user{n % 7}", "subreddit": "AskReddit" if n % 3 else "pics",
             "created_utc": 1262304000 + n * 4_000_000, "body": f"OP is right – ü {n}"} for n in range(60)]
TEXT = "".join(json.dumps(comment, ensure_ascii=False) + "\n" for comment in COMMENTS)


def write_archive(path: Path, text: str = TEXT) -> Path:
    archive = large_files.archive_of(path)
    archive.write_bytes(zstandard.ZstdCompressor().compress(text.encode("utf-8")))
    return archive


class Folder(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)


class ReadingTests(Folder):
    def test_either_form_is_found_and_the_uncompressed_one_wins(self):
        path = self.folder / "data.jsonl"
        with self.assertRaises(FileNotFoundError):
            large_files.existing(path)
        archive = write_archive(path, "old\n")
        self.assertEqual(large_files.existing(path), archive)
        self.assertEqual(large_files.existing(archive), archive)
        path.write_text("new\n", encoding="utf-8")
        self.assertEqual(large_files.existing(path), path)
        with large_files.open_text(path) as handle:
            self.assertEqual(handle.read(), "new\n")

    def test_an_archive_reads_like_the_file(self):
        path = self.folder / "data.jsonl"
        text = TEXT + "a\r\nb\rc"  # other line ends and no final one
        path.write_text(text, encoding="utf-8", newline="")
        with large_files.open_text(path) as handle:
            plain = list(handle)
        size = large_files.uncompressed_size(path)
        path.unlink()
        archive = self.folder / "data.jsonl.zst"
        compressor = zstandard.ZstdCompressor()
        raw = text.encode("utf-8")
        archive.write_bytes(compressor.compress(raw[:500]) + compressor.compress(raw[500:]))  # two frames
        with large_files.open_text(path) as handle:
            self.assertEqual(list(handle), plain)
        self.assertEqual(size, len(raw))
        write_archive(path, text)
        self.assertEqual(large_files.uncompressed_size(path), len(raw))
        with large_files.open_text(path) as handle:
            for _ in range(3):
                next(handle)
            self.assertGreater(handle.buffer.tell(), 0)  # the progress reports of step 6 use this


class PipelineReaderTests(Folder):
    """Each reader of a large file gives the same result from the archive as from the file."""

    def both_forms(self, read):
        path = self.folder / "input.jsonl"
        path.write_text(TEXT, encoding="utf-8")
        plain = read(path)
        path.unlink()
        write_archive(path)
        return plain, read(path)

    def test_step_2_filtering(self):
        plain, packed = self.both_forms(lambda path: list(filtering.iter_json_records(path)))
        self.assertEqual(plain, packed)
        self.assertEqual(len(plain), len(COMMENTS))

    def test_step_3a_grouping_and_step_4_cross_subreddit(self):
        for module in (grouping, cross_subreddit):
            plain, packed = self.both_forms(lambda path: list(module.iter_comments(path)))
            self.assertEqual(plain, packed)
            self.assertEqual(len(plain), len(COMMENTS))

    def test_step_6_baseline_sample(self):
        def sample(path: Path) -> str:
            subreddits = frozenset({"askreddit"})
            counts = baseline_reduction.count_by_quarter(path, subreddits)
            quotas = baseline_reduction.allocate_quotas(counts, 10)
            lines = baseline_reduction.select_line_numbers(path, counts, quotas, 42, subreddits)
            baseline_reduction.write_sample(path, self.folder / "sample.jsonl", lines)
            return (self.folder / "sample.jsonl").read_text(encoding="utf-8")

        plain, packed = self.both_forms(sample)
        self.assertEqual(plain, packed)
        self.assertEqual(plain.count("\n"), 10)

    def test_step_8_duckdb_reads_the_archive(self):
        def ids(path: Path) -> list:
            source = large_files.existing(path)
            return duckdb.sql(f"SELECT id FROM read_json('{source}', columns = {{id: 'VARCHAR'}}, "
                              f"{prepare_ids.JSON_OPTIONS}) ORDER BY id").fetchall()

        plain, packed = self.both_forms(ids)
        self.assertEqual(plain, packed)
        self.assertEqual(len(plain), len(COMMENTS))


class CompressionTests(Folder):
    def test_only_the_files_that_are_there_are_compressed(self):
        targets = ("02/filtered.jsonl", "03/baseline.jsonl", "08/RC/*.ndjson")
        (self.folder / "02").mkdir()
        (self.folder / "08" / "RC").mkdir(parents=True)
        (self.folder / "02" / "filtered.jsonl").write_text(TEXT, encoding="utf-8")
        (self.folder / "08" / "RC" / "RC_2010-01.ndjson").write_text(TEXT, encoding="utf-8")
        write_archive(self.folder / "08" / "RC" / "RC_2010-02.ndjson")  # already compressed
        sources = clf.find_sources(self.folder, targets)
        self.assertEqual([source.name for source in sources], ["filtered.jsonl", "RC_2010-01.ndjson"])
        for source in sources:
            self.assertIsNotNone(clf.compress(source))
            self.assertFalse(source.exists())
            with large_files.open_text(source) as handle:
                self.assertEqual(handle.read(), TEXT)
            self.assertEqual(large_files.uncompressed_size(source), len(TEXT.encode("utf-8")))
        self.assertEqual(clf.find_sources(self.folder, targets), [])  # a second run has nothing to do
        self.assertEqual(sorted(path.name for path in self.folder.rglob("*") if path.is_file()),
                         ["RC_2010-01.ndjson.zst", "RC_2010-02.ndjson.zst", "filtered.jsonl.zst"])

    def test_a_new_file_replaces_its_old_archive(self):
        path = self.folder / "baseline.jsonl"
        write_archive(path, "old\n")
        path.write_text(TEXT, encoding="utf-8")
        clf.compress(path)
        self.assertFalse(path.exists())
        with large_files.open_text(path) as handle:
            self.assertEqual(handle.read(), TEXT)

    def test_an_empty_file(self):
        path = self.folder / "RS_2005-06.ndjson"
        path.write_text("")
        clf.compress(path)
        with large_files.open_text(path) as handle:
            self.assertEqual(handle.read(), "")


if __name__ == "__main__":
    unittest.main()
