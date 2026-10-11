import json
import os
import tempfile
import unittest
from pathlib import Path

from tocab.pipeline import load_forum_targets, merge_reviews, monitor_once, prefilter_file, process_alive, write_crawl_manifest


class PipelineTests(unittest.TestCase):
    def test_forum_list_builds_deduplicated_resumable_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "forums.txt"
            source.write_text("特斯拉\n特斯拉吧\n\n原神\n", encoding="utf-8")
            self.assertEqual(load_forum_targets(source), ["特斯拉", "原神"])
            result = write_crawl_manifest(source, root / "manifest.json")
            self.assertEqual(result["forum_count"], 2)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["next_index"], 0)
            self.assertEqual(manifest["targets"][0]["url"], "https://tieba.baidu.com/f?kw=%E7%89%B9%E6%96%AF%E6%8B%89")

    def test_monitor_only_backups_changed_export_and_records_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = root / "export.json"
            state = root / "state.json"
            backups = root / "backups"
            export.write_text('{"thread": [], "post": [], "comment": []}\n', encoding="utf-8")
            first = monitor_once(state, export, backups, os.getpid(), "python")
            self.assertTrue(first["crawler_alive"])
            self.assertTrue(Path(first["last_backup"]).is_file())
            second = monitor_once(state, export, backups, os.getpid(), "python")
            self.assertEqual(first["last_backup"], second["last_backup"])
            export.write_text('{"thread": [], "post": [], "comment": [{"cid": 1}]}\n', encoding="utf-8")
            third = monitor_once(state, export, backups, os.getpid(), "python")
            self.assertNotEqual(second["last_backup"], third["last_backup"])

    def test_monitor_refuses_partial_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = root / "export.json"
            export.write_text('{"thread": [', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                monitor_once(root / "state.json", export, root / "backups")

    def test_prefilter_is_recall_oriented(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(json.dumps({
                "thread": [{"tid": 1, "title": "title"}],
                "post": [{"pid": 2, "tid": 1, "floor": 1}],
                "comment": [{"cid": 3, "pid": 2, "text": "text"}],
            }), encoding="utf-8")
            model = Path("artifacts/tocab.joblib")
            queue = root / "queue.jsonl"
            predictions = root / "predictions.jsonl"
            result = prefilter_file(source, model, queue, predictions, threshold=0.0)
            self.assertEqual(result["fine_review_candidates"], 1)
            self.assertEqual(json.loads(queue.read_text(encoding="utf-8"))["prefilter"], "needs_fine_review")

    def test_merge_rejects_incomplete_or_invalid_reviews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = root / "queue.jsonl"
            queue.write_text(json.dumps({"cid": 1}) + "\n", encoding="utf-8")
            reviews = root / "reviews.jsonl"
            reviews.write_text(json.dumps({"cid": 1, "verdict": "maybe"}) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                merge_reviews(queue, reviews, root / "out.jsonl")

    def test_merge_accepts_skill_plain_text_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = root / "queue.jsonl"
            queue.write_text(json.dumps({"cid": 1}) + "\n" + json.dumps({"cid": 2}) + "\n", encoding="utf-8")
            reviews = root / "reviews.txt"
            reviews.write_text("违禁\n不违禁\n", encoding="utf-8")
            output = root / "out.jsonl"
            result = merge_reviews(queue, reviews, output)
            self.assertEqual(result["reviewed"], 2)
            self.assertEqual([json.loads(line)["verdict"] for line in output.read_text(encoding="utf-8").splitlines()], ["违禁", "不违禁"])


if __name__ == "__main__":
    unittest.main()
