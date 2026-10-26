# 部署与数据

## 原生运行

`./scripts/bootstrap.sh` 安装三个隔离 Python 环境和前端构建。`python3 scripts/dev.py start` 启动网关、媒体引擎及工作台，45 秒内检查各自健康状态；有占用端口或运行进程时拒绝重复启动。`stop` 只终止该项目记录并核验的进程。

macOS 的精简 FFmpeg 可能没有 `subtitles` 过滤器；安装 `brew install ffmpeg-full`。启动器优先把 Homebrew `ffmpeg-full/bin` 加入 PATH。字体建议安装 Noto Sans CJK；容器内已安装。合成不会把不足时长的视频偷偷拉长；请修改分镜或重新生成。

界面位于 [127.0.0.1:8787](http://127.0.0.1:8787)，网关高级设置位于 [127.0.0.1:8766](http://127.0.0.1:8766)。服务仅为个人本地工作台设计，不能直接向公网暴露。

## OrbStack

安装并打开 OrbStack；在本机用 `docker --context orbstack info` 检查运行时。这里的 docker CLI 连接 OrbStack，使用前无需启动 Docker Desktop。

```bash
python3 scripts/dev.py stop
docker --context orbstack compose config --quiet
docker --context orbstack compose up -d --build
docker --context orbstack compose logs -f studio
docker --context orbstack compose down
```

容器只发布本机的 8787 与 8766；私有数据挂载到 `.local/`。单个服务监督三个隔离进程，不需要 Redis/Postgres。媒体规范、审核与队列持久化在 SQLite WAL 中。不要原生与容器同时写同一目录。

## 备份

停止服务后备份整个 `.local/`，其中包含数据库、原始媒体、云响应回执、网关账号配置与密钥。备份需按私有资料保护，不提交到 GitHub。恢复到同一项目的 `.local/` 后重新启动。`export.zip` 适合交付单项目内容与结果，不包含模型配置，也不能替代全量备份。

修改本机路径可设置 `FRAMEWEAVE_DATA`；修改内部服务地址可设置 `FRAMEWEAVE_GATEWAY` 与 `FRAMEWEAVE_ENGINE`。网关端口默认 8766；若被其他项目占用，在本机忽略提交的 `.env` 写入 `FRAMEWEAVE_GATEWAY_PORT=8786` 后重启。OrbStack 只调整宿主机映射，容器内部保持 8766；原生启动器也读取这个端口字段。扩展控制台链接按实际配置显示，浏览器扩展的网关地址应相应调整。开发前端：`cd frontend && npm run dev`，代理到 8787。

## 任务恢复

网页生成接受后保存 `remote_id`，恢复和轮询不会重新创建远端任务。每个规划子步骤使用独立幂等键。云模型成功响应先写本地回执，再处理和下载；连接中断且没有回执的提交会进入等待核对状态。核对供应商记录后才能显式重新生成。暂停不会强制取消已提交供应商请求；取消也不能保证退费。
