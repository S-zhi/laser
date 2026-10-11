# TOCAB 垃圾评论分类器

这是一个用于二分类垃圾评论识别的基线项目，标签为 `yes` 和 `no`。项目会先使用 OpenCC 的 `t2s.json` 将繁体中文转换为简体中文，再按原始帖子分组，使用字符级 TF-IDF 特征和带 L2 正则化的逻辑回归进行训练。`abusive_class`、投票数、看板和文章正文等字段不会作为模型特征。

## 安装

建议使用 Python 3.11，并安装项目依赖：

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

依赖版本与当前保存的模型和文本处理方式保持一致。数据准备和预测都需要 OpenCC；如果没有安装，程序会明确提示错误，不会使用其他转换方式替代。

## 准备数据

从 [TOCAB 官方页面](https://nlp.cse.ntou.edu.tw/resources/TOCAB/) 下载数据集，并将 `TOCAB_TR.json` 放到 `data/` 目录中。该页面将数据集标记为 CC BY-SA 4.0，并要求引用相关论文；重新使用或分发数据前，请先确认对应许可条件。

原始文件可以是文章列表，也可以是包含 `ptt_post` 字段的数据集包装对象。每篇文章需要包含评论列表 `comment`，以及包含帖子 ID 的 `permalink_info`。`permalink_info` 可以直接是 ID 字符串，例如 `"M.1561282423.A.D87"`，也可以是包含 `id` 字段的对象。

运行数据准备：

```sh
python -m tocab.prepare data/TOCAB_TR.json data/prepared.jsonl
```

命令行会输出保留和跳过的记录数量。生成的 JSONL 每行包含：

- `group_id`：原始帖子 ID
- `text_raw`：原始评论
- `text_simplified`：转换后的简体中文评论
- `label`：`yes` 或 `no`

如果文章没有可用的帖子 ID，程序会报错，因为无法安全地完成分组。

也可以单独检查繁体转简体的效果：

```sh
python -c 'from tocab.text import traditional_to_simplified; print(traditional_to_simplified("繁體中文"))'
```

## 训练和评估

```sh
python -m tocab.train data/prepared.jsonl \
  --model artifacts/tocab.joblib \
  --metrics artifacts/metrics.json \
  --seed 42 \
  --c 1.0
```

训练过程会按帖子进行固定的 80/20 划分，确保同一个帖子不会同时出现在训练集和验证集中。程序会尝试多个确定性的分组划分，直到训练集和验证集都包含两个类别；如果数据量不足，会明确报错。

TF-IDF 向量器只使用训练集拟合。训练完成后会输出准确率、宏平均 F1 和每个类别的评估结果。

分类器使用 scikit-learn 默认的 L2 正则化。为了兼容 scikit-learn 1.8，代码没有显式传入 `penalty` 参数，但模型配置会记录 `penalty: "l2"`。`--c` 表示正则化强度的倒数：

- `c` 越小，正则化越强；
- `c` 越大，正则化越弱。

训练得到的模型会保存到 `artifacts/tocab.joblib`，评估指标会保存到 `artifacts/metrics.json`。

## 预测

对单条已经是简体中文的评论进行预测。预测入口不会再做繁体转简体，输入文本应直接使用简体中文：

```sh
python -m tocab.predict artifacts/tocab.joblib '这是一则简体中文留言'
```

如果不提供评论参数，程序会从标准输入读取评论，每行一条：

```sh
cat comments.txt | python -m tocab.predict artifacts/tocab.joblib
```

对扁平贴吧导出文件批量预测：

```sh
python -m tocab.forum_predict \
  data/_______.json \
  artifacts/tocab.joblib \
  data/comment_predictions.jsonl
```

脚本按 `comment.pid → post.pid → post.tid → thread.tid` 关联楼层和主题帖，跳过没有文本的评论，并将每条预测结果写成一行 JSONL。导出文件中的 `text` 已经是简体中文，脚本不会再次转换。

## 稳定的贴吧自动化链路

建议把贴吧机器人当作**无状态外部采集器**，不要假设 Muse 或机器人会替你可靠保存进度。采集器写入 `export.json` 后，由本项目负责状态、备份、预筛选和结果校验；所有输出都是“先写临时文件、成功后原子替换”，进程中断不会把半个文件当成完整结果。

`sample_tieba.txt` 已接入目标清单生成器。每行一个贴吧名称，执行下面命令生成 Muse 爬虫应读取的可恢复 manifest：

```sh
.venv/bin/python -m tocab.pipeline build-manifest \
  sample_tieba.txt runtime/tieba-manifest.json
```

manifest 会保留文件顺序、自动去重，并为每个贴吧记录 `pending/done/error` 状态和分页 `cursor`。Muse 应逐个处理 `status=pending` 的目标，成功后立即保存状态；重启时从第一个未完成目标和游标继续。该项目只负责清单、状态和备份，不实现百度登录、验证码或反爬绕过。

### 1. 每 60 分钟监控和备份

让 Muse 的定时任务只做健康检查，不要在检查任务中重启一个仍然存活的进程：

```sh
.venv/bin/python -m tocab.pipeline monitor \
  --state runtime/state.json \
  --export runtime/tieba-export.json \
  --backup-dir runtime/backups \
  --pid "$TIEBA_PID" \
  --expected-command "tieba"
```

用 `launchd`、systemd timer 或 Muse 的 scheduler 每 60 分钟调用一次。`state.json` 记录 PID、检查时间、导出文件签名和 SHA-256；`runtime/backups/manifest.jsonl` 记录每一个不同版本。若 PID 不存在或命令不匹配，Muse 才进入“告警/人工确认/按机器人自身恢复协议重启”分支。不要直接按 PID 杀进程，也不要让多个调度器同时跑；脚本自带目录锁防重入。

### 2. LASER 预筛选，再交给 Muse 精筛

`yes` 只是 TOCAB 模型标签，不应直接对外宣称为最终违禁结论。预筛选默认保守地把 `prediction == yes` **或** `P(yes) >= 0.20` 的样本放入精筛队列，优先保证召回：

```sh
batch="$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/python -m tocab.pipeline prefilter \
  runtime/tieba-export.json artifacts/tocab.joblib \
  "runtime/fine-review-queue-${batch}.jsonl" "runtime/laser-predictions-${batch}.jsonl"
```

Muse 精筛输入队列后，按 `skills/tieba-moderation/SKILL.md` 逐行只输出 `违禁` 或 `不违禁`。`merge-reviews` 会按队列顺序把这些纯文本结果与 `cid` 绑定；如果你的调度器能保留 ID，也接受 `{"cid": 123, "verdict": "违禁"}` JSONL。校验并合并：

```sh
batch="20261011T000000Z"  # replace with the batch suffix used above
.venv/bin/python -m tocab.pipeline merge-reviews \
  "runtime/fine-review-queue-${batch}.jsonl" runtime/muse-reviews.txt \
  "runtime/final-verdicts-${batch}.jsonl"
```

合并会拒绝未知/重复/缺失 `cid`，也只接受 `违禁` 和 `不违禁`。最后给下游系统的定时任务使用：

```sh
.venv/bin/python -m tocab.pipeline emit "runtime/final-verdicts-${batch}.jsonl"
```

这个命令每行只输出 `违禁` 或 `不违禁`，不输出解释、概率或其他字段，满足严格的 Skill 输出约束。建议保留原始采集快照、LASER 预测、精筛输入和最终结果四层数据，设置磁盘配额和轮转策略；涉及个人信息时应脱敏、限制访问并遵守贴吧条款、适用法律和数据集许可。
