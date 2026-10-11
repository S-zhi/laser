"""Train and evaluate a post-grouped TOCAB comment classifier."""
import argparse
import json
import math
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import GroupShuffleSplit


def load_jsonl(path: Path):
    """读取准备阶段生成的 JSONL，并在训练前做结构校验。"""
    rows = []
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON") from exc
            if (not isinstance(row, dict) or not isinstance(row.get("text_simplified"), str)
                    or not row["text_simplified"].strip() or row.get("label") not in ("yes", "no")
                    or not isinstance(row.get("group_id"), str) or not row["group_id"]):
                raise ValueError(f"{path}:{number}: expected nonempty text_simplified, yes/no label, group_id")
            # 训练只依赖这三个字段；其余字段会随 row 保留，但不会参与特征构造。
            rows.append(row)
    if not rows:
        raise ValueError(f"{path}: no rows")
    return rows


def split_groups(rows, test_size=0.2, seed=42):
    """按帖子分割评论，确保同一 group_id 不会跨越训练集和验证集。"""
    # 标签按评论保存，group_id 按帖子保存；GroupShuffleSplit 会整体移动帖子。
    groups = np.asarray([row["group_id"] for row in rows])
    labels = np.asarray([row["label"] for row in rows])
    if len(set(groups)) < 2:
        raise ValueError("at least two distinct post groups are required")
    splitter = GroupShuffleSplit(n_splits=500, test_size=test_size, random_state=seed)
    # 随机尝试多个固定种子的划分，直到两边都同时包含 yes/no 两类。
    for train, valid in splitter.split(np.zeros(len(rows)), labels, groups):
        if set(labels[train]) == {"yes", "no"} and set(labels[valid]) == {"yes", "no"}:
            return train, valid
    raise ValueError("could not find a group split with both labels in train and validation; add more posts/classes")


def evaluate_predictions(labels, predictions):
    """Return the binary classification metrics used by the training CLI.

    Keeping metric computation separate makes it usable from notebooks and
    tests, while ensuring that the persisted training metrics and any later
    evaluation use the same label ordering and zero-division behavior.
    """
    labels = np.asarray(labels)
    predictions = np.asarray(predictions)
    if labels.shape != predictions.shape:
        raise ValueError("labels and predictions must have the same shape")
    # 显式固定类别顺序，保证每次保存的报告结构一致。
    report = classification_report(
        labels, predictions, labels=["no", "yes"], output_dict=True, zero_division=0
    )
    return {
        "accuracy": float(accuracy_score(labels, predictions)),
        "macro_f1": float(f1_score(labels, predictions, average="macro")),
        "classification_report": report,
    }


def train(data_path: Path, model_path: Path, metrics_path: Path, seed=42, c=1.0):
    """Train a character TF-IDF binary classifier with L2 regularization.

    ``c`` is sklearn's inverse regularization strength. Smaller values apply
    stronger L2 regularization; it must be positive.
    """
    if not math.isfinite(c) or c <= 0:
        raise ValueError("c must be a finite positive number")
    # 此时数据已经是 comment-level JSONL；加载后先按帖子切分，再拟合文本特征。
    rows = load_jsonl(data_path)
    train_idx, valid_idx = split_groups(rows, seed=seed)
    texts = np.asarray([r["text_simplified"] for r in rows], dtype=object)
    labels = np.asarray([r["label"] for r in rows])
    groups = np.asarray([r["group_id"] for r in rows])
    # 字符 n-gram 能处理中文文本；vectorizer 只在训练集上 fit，避免验证集信息泄漏。
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 4), sublinear_tf=True, max_features=300000)
    x_train = vectorizer.fit_transform(texts[train_idx])
    # LogisticRegression 默认使用 L2；C 是正则化强度的倒数，越小表示正则越强。
    classifier = LogisticRegression(
        C=c,
        solver="liblinear",
        max_iter=1000,
        random_state=seed,
    )
    classifier.fit(x_train, labels[train_idx])
    # 验证集只做 transform，不重新 fit，然后用训练好的分类器预测。
    pred = classifier.predict(vectorizer.transform(texts[valid_idx]))
    evaluated = evaluate_predictions(labels[valid_idx], pred)
    metrics = {
        "seed": seed, "l2_inverse_strength": float(c),
        "train_comments": int(len(train_idx)), "validation_comments": int(len(valid_idx)),
        "train_posts": int(len(set(groups[train_idx]))), "validation_posts": int(len(set(groups[valid_idx]))),
        **evaluated,
    }
    # 模型包保存预处理器和分类器，指标单独保存，便于后续预测和比较实验。
    model_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "vectorizer": vectorizer,
        "classifier": classifier,
        "normalization": "opencc:t2s.json",
        "regularization": {"penalty": "l2", "C": float(c)},
    }, model_path)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metrics


def main():
    """解析 CLI 参数并启动一次训练。"""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="Prepared JSONL")
    p.add_argument("--model", type=Path, default=Path("artifacts/tocab.joblib"))
    p.add_argument("--metrics", type=Path, default=Path("artifacts/metrics.json"))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--c", type=float, default=1.0,
                   help="Inverse L2 regularization strength (smaller is stronger)")
    a = p.parse_args()
    print(json.dumps(train(a.input, a.model, a.metrics, a.seed, a.c), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
