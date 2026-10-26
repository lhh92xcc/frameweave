# 上游升级约定

`upstream-lock.json` 与 Git submodule 提交号共同记录发布所用的精确版本。仓库递归克隆即可复现，不从父目录复制本地 `.env`、Cookie、媒体或未提交实验。

## 为什么不把两个源码目录混在一起

两个 Python 项目存在同名模块及不同依赖版本。FrameWeave、视频引擎与网关使用三个隔离环境，通过本机 HTTP 契约连接。前端及编排定制不会产生上游文件冲突。新连接器读取网关目录；新的协议需要适配层和测试一起更新。

`services/engine_bridge.py` 复用引擎的 `split_chapters`、媒体 validator、`EdgeTTSProvider`、`FFmpegVideoRenderer` 和字幕原语；`services/renderer.py` 隔离画幅和字幕兼容处理。`studio/providers.py` 对接网关异步任务、参考图上传、状态查询、结果下载与账号管理接口。上游源代码维持干净状态。

## 命令

- `python3 scripts/upstream.py check`：拉取两边远端，比较固定版本与 main。
- `python3 scripts/upstream.py update web2api-web`：只升级网关。
- `python3 scripts/upstream.py update ai-video-generation`：只升级引擎。
- `python3 scripts/upstream.py update all`：两边升级。

更新前停止原生服务或 OrbStack 容器，备份 `.local/`。脚本检测运行服务，并拒绝覆盖子模块已修改的跟踪文件；升级后保留新 pin 安装依赖、重建界面、跑全部后端测试。失败恢复旧提交并重新安装旧依赖；应用数据不被更新脚本重写。成功后查看 `git diff --submodule=log`，提交 pin 与 lock 并推送。容器用户随后执行 `docker --context orbstack compose up -d --build`，把新源码构建进镜像。

源代码回滚：`git restore --source=<known-good-commit> -- upstream upstream-lock.json`，然后 `git submodule update --init --recursive`、`./scripts/bootstrap.sh`。数据库结构升级应另带显式版本迁移；源码回滚不能替代数据备份。

GitHub 的 `Update upstreams` 手动工作流会在独立分支验证并创建更新 PR，不会直接覆盖 main。升级不保证全部网页平台可用；账号状态与网页协议变化需要真实账号验证。
