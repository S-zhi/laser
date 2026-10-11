"""Safe, resumable operations for a Tieba -> LASER -> fine-review pipeline.

The crawler is deliberately treated as an external process.  This module does
not pretend that Muse or a Tieba bot persists state for us; it records a
supervisor checkpoint, snapshots changed exports, and keeps every downstream
write atomic so a restart can resume from the last complete file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from .forum_predict import predict_file

VERDICTS = {"违禁", "不违禁"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: object) -> None:
    """Write JSON beside the target, then replace it in one filesystem step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _atomic_jsonl(path: Path, rows: Iterator[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    count = 0
    try:
        with temporary.open("w", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
                count += 1
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return count


@contextmanager
def exclusive_lock(path: Path, stale_after: int = 6 * 3600):
    """Use an atomic directory as a portable lock for scheduled invocations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir()
    except FileExistsError:
        try:
            if time.time() - path.stat().st_mtime > stale_after:
                path.rmdir()
                path.mkdir()
            else:
                raise RuntimeError(f"another pipeline run holds {path}")
        except FileNotFoundError:
            path.mkdir()
    try:
        (path / "owner").write_text(f"pid={os.getpid()}\nstarted={_now()}\n", encoding="utf-8")
        yield
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_forum_targets(path: Path) -> list[str]:
    """Load one Tieba name per line, normalize an optional trailing ``吧``.

    Order is preserved and duplicates are removed so a scheduler can safely
    retry the same manifest without crawling a forum twice in one batch.
    """
    targets: list[str] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig") as source:
        for number, raw in enumerate(source, 1):
            name = raw.strip()
            if not name:
                continue
            if name.endswith("吧"):
                name = name[:-1].strip()
            if not name:
                raise ValueError(f"{path}:{number}: forum name is empty")
            if "\n" in name or "\r" in name:
                raise ValueError(f"{path}:{number}: forum name contains a newline")
            if name not in seen:
                seen.add(name)
                targets.append(name)
    if not targets:
        raise ValueError(f"{path}: no forum names")
    return targets


def write_crawl_manifest(forum_list: Path, manifest: Path) -> dict:
    """Create the Muse crawler's deterministic, resumable target manifest."""
    targets = load_forum_targets(forum_list)
    rows = [
        {
            "name": name,
            "url": f"https://tieba.baidu.com/f?kw={quote(name)}",
            "status": "pending",
            "cursor": None,
            "last_error": None,
        }
        for name in targets
    ]
    document = {
        "version": 1,
        "source": str(forum_list),
        "generated_at": _now(),
        "next_index": 0,
        "targets": rows,
    }
    _atomic_json(manifest, document)
    return {"manifest": str(manifest), "forum_count": len(targets), "forums": targets}


def process_alive(pid: int, expected_command: str | None = None) -> bool:
    """Check a PID and, when supplied, guard against PID reuse."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        # EPERM means the process exists but belongs to another user.
        return True
    except (ProcessLookupError, OSError):
        return False
    if not expected_command:
        return True
    try:
        command = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        # A process that cannot be inspected is still considered alive; the
        # next monitor run will retry the stronger command check.
        return True
    return expected_command in command


def snapshot_export(
    export: Path,
    backup_dir: Path,
    state: dict,
) -> dict:
    """Snapshot a changed crawler export and return updated checkpoint data."""
    if not export.is_file():
        raise FileNotFoundError(f"crawler export does not exist: {export}")
    stat = export.stat()
    signature = f"{stat.st_size}:{stat.st_mtime_ns}"
    if signature == state.get("export_signature"):
        state["last_backup"] = state.get("last_backup")
        return state
    digest = _sha256(export)
    if digest == state.get("export_sha256"):
        state["export_signature"] = signature
        return state

    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"export-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{digest[:12]}.json"
    temporary = target.with_suffix(".tmp")
    shutil.copy2(export, temporary)
    # A crawler may still be writing its export.  Never publish a truncated
    # snapshot; the next scheduled check will retry after the writer settles.
    try:
        copied_digest = _sha256(temporary)
        if copied_digest != digest:
            raise ValueError("export changed during snapshot")
        with temporary.open(encoding="utf-8") as source:
            document = json.load(source)
        if not isinstance(document, dict) or any(not isinstance(document.get(table), list) for table in ("thread", "post", "comment")):
            raise ValueError("export is not a complete Tieba document")
    except (OSError, json.JSONDecodeError, ValueError):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"crawler export is incomplete; backup skipped: {export}")
    os.replace(temporary, target)
    manifest = backup_dir / "manifest.jsonl"
    with manifest.open("a", encoding="utf-8") as output:
        output.write(json.dumps({"time": _now(), "source": str(export), "sha256": digest, "path": str(target)}, ensure_ascii=False) + "\n")
    state.update({"export_signature": signature, "export_sha256": digest, "last_backup": str(target)})
    return state


def monitor_once(
    state_path: Path,
    export: Path,
    backup_dir: Path,
    pid: int | None = None,
    expected_command: str | None = None,
) -> dict:
    """Record crawler health and make a backup without holding a long lock."""
    state = {}
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    alive = process_alive(pid, expected_command) if pid is not None else None
    state.update({"checked_at": _now(), "crawler_pid": pid, "crawler_alive": alive})
    state = snapshot_export(export, backup_dir, state)
    _atomic_json(state_path, state)
    return state


def prefilter_file(
    source: Path,
    model: Path,
    queue: Path,
    predictions: Path,
    threshold: float = 0.20,
) -> dict:
    """Run LASER and queue only recall-oriented candidates for fine review."""
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    stats = predict_file(source, model, predictions)
    candidates = []
    with predictions.open(encoding="utf-8") as source_rows:
        for line in source_rows:
            row = json.loads(line)
            probability = float(row.get("probabilities", {}).get("yes", 0.0))
            if row.get("prediction") == "yes" or probability >= threshold:
                row["prefilter"] = "needs_fine_review"
                row["model_yes_probability"] = probability
                candidates.append(row)
    written = _atomic_jsonl(queue, iter(candidates))
    return {**dict(stats), "prefilter_threshold": threshold, "fine_review_candidates": written}


def merge_reviews(queue: Path, reviews: Path, output: Path) -> dict:
    """Validate Muse fine-review results and persist an auditable verdict file.

    The preferred review format is one bare verdict per line, because the
    moderation Skill is required to emit only ``违禁``/``不违禁``.  JSONL with
    explicit ``cid`` is also accepted for integrations that preserve IDs.
    """
    queue_rows: list[dict] = []
    with queue.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            queue_rows.append(row)
    expected = {str(row["cid"]): row for row in queue_rows}
    seen: set[str] = set()
    merged = []
    with reviews.open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            line = line.strip()
            if not line:
                raise ValueError(f"review line {number}: empty verdict")
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                row = line
            if isinstance(row, str):
                if number > len(queue_rows):
                    raise ValueError(f"review line {number}: too many verdicts")
                cid = str(queue_rows[number - 1]["cid"])
                verdict = row
            elif isinstance(row, dict):
                cid = str(row.get("cid"))
                verdict = row.get("verdict")
            else:
                raise ValueError(f"review line {number}: expected bare verdict or JSON object")
            if cid not in expected:
                raise ValueError(f"review line {number}: unknown cid {cid}")
            if cid in seen:
                raise ValueError(f"review line {number}: duplicate cid {cid}")
            if verdict not in VERDICTS:
                raise ValueError(f"review line {number}: verdict must be 违禁 or 不违禁")
            seen.add(cid)
            merged.append({"cid": expected[cid].get("cid"), "verdict": verdict})
    if seen != set(expected):
        raise ValueError(f"fine review incomplete: {len(set(expected) - seen)} candidates remain")
    _atomic_jsonl(output, iter(merged))
    return {"candidates": len(expected), "reviewed": len(merged), "output": str(output)}


def emit_verdicts(path: Path) -> int:
    """Print exactly one allowed Chinese verdict per line, and nothing else."""
    count = 0
    with path.open(encoding="utf-8") as source:
        for line in source:
            verdict = json.loads(line).get("verdict")
            if verdict not in VERDICTS:
                raise ValueError("invalid stored verdict")
            print(verdict)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    monitor = sub.add_parser("monitor", help="health check and atomic export backup")
    monitor.add_argument("--state", type=Path, required=True)
    monitor.add_argument("--export", type=Path, required=True)
    monitor.add_argument("--backup-dir", type=Path, required=True)
    monitor.add_argument("--pid", type=int)
    monitor.add_argument("--expected-command")

    manifest = sub.add_parser("build-manifest", help="connect sample_tieba.txt to the crawler")
    manifest.add_argument("forum_list", type=Path)
    manifest.add_argument("output", type=Path)

    prefilter = sub.add_parser("prefilter", help="LASER prefilter")
    prefilter.add_argument("input", type=Path)
    prefilter.add_argument("model", type=Path)
    prefilter.add_argument("queue", type=Path)
    prefilter.add_argument("predictions", type=Path)
    prefilter.add_argument("--threshold", type=float, default=0.20)

    merge = sub.add_parser("merge-reviews", help="validate Muse review results")
    merge.add_argument("queue", type=Path)
    merge.add_argument("reviews", type=Path)
    merge.add_argument("output", type=Path)

    emit = sub.add_parser("emit", help="emit only 违禁/不违禁")
    emit.add_argument("input", type=Path)
    args = parser.parse_args()
    if args.command == "build-manifest":
        print(json.dumps(write_crawl_manifest(args.forum_list, args.output), ensure_ascii=False, indent=2))
    elif args.command == "monitor":
        with exclusive_lock(args.state.with_suffix(args.state.suffix + ".lock")):
            result = monitor_once(args.state, args.export, args.backup_dir, args.pid, args.expected_command)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "prefilter":
        print(json.dumps(prefilter_file(args.input, args.model, args.queue, args.predictions, args.threshold), ensure_ascii=False, indent=2))
    elif args.command == "merge-reviews":
        print(json.dumps(merge_reviews(args.queue, args.reviews, args.output), ensure_ascii=False, indent=2))
    else:
        emit_verdicts(args.input)


if __name__ == "__main__":
    main()
