"""Flatten raw TOCAB article JSON into comment-level JSONL."""
import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Iterator

from .text import traditional_to_simplified


def records(raw: object, counts: Counter | None = None) -> Iterator[dict]:
    # 原始 TOCAB 有两种外层形态：直接是文章列表，或包在 ``ptt_post`` 中。
    # 这里先统一成文章列表，后面的逻辑就不需要关心外层包装格式。
    counts = counts if counts is not None else Counter()
    if isinstance(raw, dict):
        raw = raw.get("ptt_post")
        if not isinstance(raw, list):
            raise ValueError("TOCAB object must contain a ptt_post article list")
    if not isinstance(raw, list):
        raise ValueError("TOCAB root must be a list or an object containing ptt_post")
    for article in raw:
        # 单篇文章损坏时跳过该文章，并通过计数器保留可观察的诊断信息。
        if not isinstance(article, dict):
            counts["invalid_articles"] += 1
            continue
        info = article.get("permalink_info")
        if not info:
            raise ValueError("article missing permalink_info; cannot assign group_id")
        # group_id 用于训练阶段按帖子切分数据，避免同一帖子的评论泄漏到两边。
        group_id = str(info.get("id", "") if isinstance(info, dict) else info).strip()
        if not group_id:
            raise ValueError("article permalink_info has no usable post ID")
        comments = article.get("comment", [])
        if not isinstance(comments, list):
            counts["invalid_comment_lists"] += 1
            continue
        for comment in comments:
            counts["comments_seen"] += 1
            # 只保留同时具备非空文本和合法二分类标签的评论。
            if not isinstance(comment, dict):
                counts["invalid_comments"] += 1
                continue
            text = comment.get("sentence")
            label = comment.get("abusive")
            if not isinstance(text, str) or not text.strip():
                counts["invalid_text"] += 1
                continue
            if label not in ("yes", "no"):
                counts["invalid_label"] += 1
                continue
            counts["kept"] += 1
            # 同时保存原文和简体中文：原文便于追溯，简体版本供训练/预测统一使用。
            yield {
                "group_id": group_id,
                "text_raw": text,
                "text_simplified": traditional_to_simplified(text),
                "label": label,
            }


def prepare(source: Path, target: Path) -> Counter:
    # 目标目录可能还不存在，因此在写 JSONL 前先创建目录。
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open(encoding="utf-8") as f:
        raw = json.load(f)
    counts = Counter()
    with target.open("w", encoding="utf-8") as out:
        # records 是生成器，逐条写出可以避免额外构造完整的中间列表。
        for row in records(raw, counts):
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Raw TOCAB_TR.json")
    parser.add_argument("output", type=Path, help="Prepared comment JSONL")
    args = parser.parse_args()
    counts = prepare(args.input, args.output)
    print(json.dumps(dict(counts), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
