# 工作台接口

本机 API 与界面同源于 `http://127.0.0.1:8787`，交互式 Schema 位于 `/docs`。所有修改只允许本地工作台 Origin；密钥为写入型字段，读取配置只返回 `configured`。服务不面向公网多租户部署。

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 网关、引擎、工作线程状态 |
| `GET /api/templates` | 六类创作模板及规范 |
| `GET/POST /api/projects` | 项目列表/导入原文并分章 |
| `GET /api/projects/{id}` | 项目完整工作区、任务和素材 |
| `PUT /api/projects/{id}/contract` | Markdown 新版本；需 `expected_revision` |
| `POST /api/projects/{id}/contract/approve` | 批准指定 SHA-256：`{hash}` |
| `GET /api/projects/{id}/contract.md` | 下载规范 |
| `PUT /api/projects/{id}/plan` | 编辑已校验计划：`{plan, expected_revision}` |
| `POST /api/projects/{id}/plan/approve` | 批准分镜版本：`{revision}` |
| `POST /api/projects/{id}/jobs` | 异步步骤：`{stage, entity_id, force}` |
| `POST /api/projects/{id}/automation` | 开关自动推进：`{enabled}` |
| `POST /api/projects/{id}/pause`、`resume`、`cancel` | 项目控制；暂停不杀供应商调用 |
| `POST /api/projects/{id}/upload` | multipart：`stage`、`entity_id`、`file`；导入待审核素材 |
| `GET /api/projects/{id}/export.zip` | 原文、规范、计划、请求快照、结果资料 |
| `GET /api/jobs`、`GET /api/jobs/{id}` | 队列、日志、完整提示词与远端 ID |
| `POST /api/jobs/{id}/cancel`、`retry` | 取消 / 继续查询 / 显式重试 |
| `GET /api/artifacts` | 所有项目素材及过期状态 |
| `GET /api/artifacts/{id}/content` | 图片、视频、音频原始文件 |
| `GET /api/artifacts/{id}/thumbnail` | 历史图片的小尺寸预览 |
| `POST /api/artifacts/{id}/review` | `{approved, note}`；旧版本不可批准 |
| `GET/PUT /api/settings` | 模型渠道、付费开关、声音与字幕 |
| `GET /api/gateway/catalog` | 上游当前连接器目录 |
| `/api/gateway/accounts`、`web-image-providers`、`video-providers` | 受限制的本机账号管理转发 |
| `GET /api/upstreams` | 上游固定版本 |

阶段 `stage`：`requirements`、`plan`、`reference`、`keyframe`、`video`、`audio`、`render`。图片/视频阶段要求规范与分镜批准；关键帧要求引用素材批准；视频要求关键帧批准；合成要求全部输入视频及独立配音批准。

项目创建字段包括 `title`、`mode`、`source`、`brief`、`episodes`（1–50）、`duration`（每集5–600秒）、`aspect_ratio`、`language`、`style`。分镜计划 Schema 见 OpenAPI；所有素材、集与镜头 ID 全局唯一；各集时长精确匹配，来源章节全覆盖。

任务状态：`queued`、`running`、`succeeded`、`blocked`、`failed`、`cancelled`。素材审核：`pending`、`approved`、`rejected`；`stale` 是相对当前规范、分镜和引用版本计算的状态。执行成功不自动批准。

本机历史迁移另有只读任务阶段 `archive`。素材的 `archived`、`title`、`thumbnail_url` 与 `metadata.imported` 用于历史浏览，原始审核结论单独保存，不允许通过审核接口将档案直接批准为生产素材。项目 `import_summary` 记录数量、来源分组和文件校验异常；迁移步骤见 [IMPORT.md](IMPORT.md)。项目 ZIP 包含迁移清单和制作文档，使用磁盘临时文件响应。

通用应用错误：`{error:{code,message}}`；404 为缺失，409 为版本冲突，422 为输入或前置条件问题，503 为供应商/服务不可用。HTTP Schema 校验错误由 FastAPI 的 `detail` 返回。供应商原始鉴权响应不会写入工作台错误正文。所有运行数据与凭据禁止提交 Git。
