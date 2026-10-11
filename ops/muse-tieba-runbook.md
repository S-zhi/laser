# Muse 定时运行手册

把下面三个任务注册到 Muse 的 scheduler。调度器本身可以重试，但每次任务都必须是幂等的：先检查锁和状态，再写临时文件，成功后替换目标文件。

## 0. 生成贴吧采集清单

`sample_tieba.txt` 现在是爬虫的唯一目标来源：每行一个名称，脚本会去掉误写的末尾“吧”、去重并生成可恢复的 `runtime/tieba-manifest.json`。Muse 不应把这 100 个名称拼接到提示词里，也不应自行修改清单。

```sh
.venv/bin/python -m tocab.pipeline build-manifest \
  sample_tieba.txt runtime/tieba-manifest.json
```

将下面的提示词交给贴吧采集器任务：

> 读取 `runtime/tieba-manifest.json`，按 `targets` 原顺序逐个采集。一次只处理一个 `status=pending` 的目标；成功后立刻把该目标标为 `done` 并保存分页游标，失败则标为 `error` 并记录 `last_error`，不要跳过后继续伪造成功。每个目标写入临时文件后原子合并到 `runtime/tieba-export.json`。进程重启时从 manifest 中第一个非 done 目标和保存的 cursor 继续。不要修改 `sample_tieba.txt`，不要重复采集 done 目标，不要删除已有导出数据。

这里接入的是**目标清单和恢复协议**，不是凭空实现百度登录、验证码或反爬绕过；这些必须由你实际使用的合规采集器提供。运行前确认已获得必要授权并遵守百度贴吧条款。

## A. 采集进程监控（每 60 分钟）

任务提示词：

> 执行 `tocab.pipeline monitor`。读取 `runtime/state.json`，检查 Tieba 采集器 PID 和 `runtime/tieba-export.json`。如果采集器仍在运行，只记录状态并备份发生变化的导出文件；不要重复启动、不要删除原始数据。如果进程不存在，先告警并保留现场，只有在确认采集器的恢复命令和登录状态都可用时才恢复。任务结束后报告 state 文件和最新 backup 路径。

命令：

```sh
cd /path/to/laser
.venv/bin/python -m tocab.pipeline monitor \
  --state runtime/state.json \
  --export runtime/tieba-export.json \
  --backup-dir runtime/backups \
  --pid "$TIEBA_PID" \
  --expected-command "tieba"
```

## B. LASER 预筛（按采集批次，或每 60 分钟监控后触发）

> 仅对本次备份对应的完整导出运行 LASER。输出预测文件和精筛队列；不要覆盖历史批次。使用默认 `P(yes) >= 0.20` 的召回优先阈值。队列完成后只报告候选数量和文件路径。

```sh
batch="$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/python -m tocab.pipeline prefilter \
  runtime/tieba-export.json artifacts/tocab.joblib \
  "runtime/fine-review-queue-${batch}.jsonl" "runtime/laser-predictions-${batch}.jsonl"
```

## C. 精筛（按队列触发）

加载 `skills/tieba-moderation/SKILL.md`。逐条读取 `text`，保持队列顺序，每条只输出 `违禁` 或 `不违禁`。禁止理由、JSON、Markdown、概率和其他文字。保存为 `runtime/muse-reviews.txt` 后执行：

```sh
batch="20261011T000000Z"  # replace with the batch suffix used above
.venv/bin/python -m tocab.pipeline merge-reviews \
  "runtime/fine-review-queue-${batch}.jsonl" runtime/muse-reviews.txt \
  "runtime/final-verdicts-${batch}.jsonl"
.venv/bin/python -m tocab.pipeline emit "runtime/final-verdicts-${batch}.jsonl"
```

如果数量不一致、出现空行、未知 `cid` 或其他输出，任务失败而不是猜测。保留原始快照、LASER 预测、队列和最终结果；这些文件包含用户内容，应限制权限并按保留期限轮转。

