# Muse 远程部署说明

仓库地址：`https://github.com/S-zhi/laser`

这是私有仓库。Muse 远程环境需要先确认已经配置了对 `S-zhi/laser` 的读取权限，不要把 GitHub Token 写入日志、代码或任务输出。

## 初始化

```sh
git clone https://github.com/S-zhi/laser.git
cd laser
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

验证代码：

```sh
python -m unittest discover -v
```

## 生成贴吧清单

```sh
python -m tocab.pipeline build-manifest \
  sample_tieba.txt runtime/tieba-manifest.json
```

采集器应读取 `runtime/tieba-manifest.json`，按 `targets` 顺序逐个处理 `status=pending` 的目标。每个目标完成后保存 `done` 和分页 `cursor`；失败时保存 `error` 与 `last_error`。采集状态和导出数据应写到 `runtime/`，不要提交回仓库。

## 重要限制

本仓库提供 manifest、状态、备份、LASER 预筛和 Muse 精筛结果校验；仓库不包含百度登录、验证码处理或具体贴吧爬虫实现。远程环境必须提供一个已经授权、合规且可运行的采集器，并把它产生的完整导出写到 `runtime/tieba-export.json`。

`artifacts/` 和 `data/` 被 `.gitignore` 排除。若远程要运行 LASER 预筛，还需要通过受控方式提供兼容的 `artifacts/tocab.joblib`，不要把包含原始评论或用户信息的数据集直接提交到公共位置。

