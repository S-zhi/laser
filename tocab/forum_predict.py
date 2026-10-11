"""Predict labels for comments in the flat Tieba export JSON."""

import argparse
import json
import os
import sys
from collections import Counter
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator

import joblib


def load_export(path: Path) -> dict:
    """Load and minimally validate the four-table export document."""
    with path.open(encoding="utf-8") as source:
        document = json.load(source)
    if not isinstance(document, dict):
        raise ValueError("export root must be a JSON object")
    for table in ("thread", "post", "comment"):
        if not isinstance(document.get(table), list):
            raise ValueError(f"export must contain a {table} array")
    return document


def _key(value: object) -> str:
    """Normalize numeric/string IDs so table joins use one key type."""
    return "" if value is None else str(value)


def comment_rows(document: dict, stats: Counter | None = None) -> Iterator[dict]:
    """Yield non-empty comments enriched through post and thread relationships."""
    stats = stats if stats is not None else Counter()
    posts = {_key(row.get("pid")): row for row in document["post"] if isinstance(row, dict)}
    threads = {_key(row.get("tid")): row for row in document["thread"] if isinstance(row, dict)}

    for comment in document["comment"]:
        stats["comments_seen"] += 1
        if not isinstance(comment, dict):
            stats["invalid_comments"] += 1
            continue
        text = comment.get("text")
        if not isinstance(text, str) or not text.strip():
            stats["empty_text"] += 1
            continue

        post = posts.get(_key(comment.get("pid")))
        # 按导出的关系链从 comment.pid 找到 post，再以 post.tid 关联 thread。
        # 如果楼层缺失，才退回 comment 自带的 tid，尽量保留可预测记录。
        tid = post.get("tid") if post is not None and post.get("tid") is not None else comment.get("tid")
        thread = threads.get(_key(tid))
        if post is None:
            stats["missing_post"] += 1
        if thread is None:
            stats["missing_thread"] += 1

        yield {
            "cid": comment.get("cid"),
            "pid": comment.get("pid"),
            "tid": tid,
            "reply_to_id": comment.get("reply_to_id", 0),
            "text": text,
            "author_id": comment.get("author_id"),
            "author_level": comment.get("author_level"),
            "create_time": comment.get("create_time"),
            "fid": comment.get("fid"),
            "post_floor": post.get("floor") if post else None,
            "thread_title": thread.get("title") if thread else None,
        }


def predict_file(source: Path, model_path: Path, output: Path, batch_size: int = 2048) -> Counter:
    """Predict comments in batches and atomically replace the JSONL output.

    Writing beside the destination and replacing it only after the complete
    prediction succeeds keeps a previous snapshot usable when a scheduled run
    is interrupted halfway through a large export.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    document = load_export(source)
    bundle = joblib.load(model_path)
    vectorizer = bundle["vectorizer"]
    classifier = bundle["classifier"]
    stats = Counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    pending: list[dict] = []
    temporary_path: Path | None = None

    def flush(out) -> None:
        if not pending:
            return
        texts = [row["text"] for row in pending]
        vectors = vectorizer.transform(texts)
        labels = classifier.predict(vectors)
        probabilities = classifier.predict_proba(vectors) if hasattr(classifier, "predict_proba") else None
        for index, row in enumerate(pending):
            result = dict(row)
            result["prediction"] = str(labels[index])
            if probabilities is not None:
                result["probabilities"] = {
                    str(label): float(probabilities[index][column])
                    for column, label in enumerate(classifier.classes_)
                }
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            stats["predicted"] += 1
        pending.clear()

    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as out:
            temporary_path = Path(out.name)
            for row in comment_rows(document, stats):
                pending.append(row)
                if len(pending) >= batch_size:
                    flush(out)
            flush(out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary_path, output)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(FileNotFoundError):
                temporary_path.unlink()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Flat export JSON")
    parser.add_argument("model", type=Path, help="Trained joblib model")
    parser.add_argument("output", type=Path, help="Output JSONL")
    parser.add_argument("--batch-size", type=int, default=2048)
    args = parser.parse_args()
    stats = predict_file(args.input, args.model, args.output, args.batch_size)
    print(json.dumps(dict(stats), ensure_ascii=False, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
