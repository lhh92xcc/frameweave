# 在另一台 Mac 恢复工作区

代码从 GitHub 拉取；媒体和加密的项目状态从私有 COS 恢复。配置密钥使用单独的加密 Markdown 备份，不进入 GitHub。此流程恢复一个明确时间点的快照，不是两台电脑实时协同。

1. `git clone --recurse-submodules https://github.com/lhh92xcc/frameweave.git`，或已有仓库执行 `git pull --ff-only` 和 `git submodule update --init --recursive`。
2. 安装依赖，按加密配置 Markdown 恢复 `.local/settings.json` 和 `.local/cloud-storage.json`。
3. 将公司端给你的 `workspace-ticket.json` 放进 `.local/`，在仓库根目录运行：
   ```bash
   uv run --with cryptography python scripts/restore_workspace.py .local/workspace-ticket.json
   ```
   输入单独保管的恢复密码。脚本校验所有下载的 SHA-256，解密项目数据库和声音绑定，恢复到新的 `.local/restored-workspace/`，不覆盖正在使用的数据。
4. 停止工作台；将恢复目录中的 `studio.sqlite3`、`media/`、`thumbnails/`、`voice-library/`、`receipts/` 等移入家里 `.local/` 的对应位置。若已有数据，先整体备份，勿直接混合 SQLite 或旧 WAL 文件。新电脑不得沿用其他数据库的 `studio.sqlite3-wal` / `studio.sqlite3-shm`。
5. 启动工作台。恢复快照内项目均暂停，待处理任务保留运行记录但不会自动继续；核对项目和资源后再决定恢复。公司端原状态不受影响。

云端对象需要长期保留；生命周期权限不足时，应在腾讯云控制台确认存储前缀没有自动过期规则。不要删除公司电脑唯一副本。COS 请求、存储和下载流量按供应商计费。

本次快照票据：`.local/exports/workspace-ticket.json`。不要提交到 GitHub；把它单独带回家。它指向 COS 中经校验的完整资源清单和加密项目状态。
