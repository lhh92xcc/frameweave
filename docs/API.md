# 工作台接口

MiniMax 配音使用 `audio_mode: "cloud"`、`audio.provider: "minimax"`、`audio.transport: "cloud"`、固定 `audio.base_url: "https://api.minimaxi.com/v1"`，默认模型 `speech-2.8-turbo`。全局 `voice` 和 `speech: {speed, vol, pitch, emotion}` 为未绑定镜头提供默认声线。`audio-cast.voices` 以人物素材 ID 或 `narrator` 为键，值为 `{voice_id, speed?, vol?, pitch?, emotion?}`；`speakers` 将镜头 ID 映射到已配置角色。快照保存生产规范与参考绑定，不包含 Key。音轨与成片按声音配置判断过期；更换 Key 不影响已完成音轨。字段范围及恢复见 [MiniMax](MINIMAX.md)。参数校验只返回字段位置、类型和说明，不回显输入数据。

本机 API 与界面同源于 `http://127.0.0.1:8787`，交互式 Schema 位于 `/docs`。所有修改只允许本地工作台 Origin；密钥为写入型字段，读取配置只返回 `configured`。服务不面向公网多租户部署。

角色 `VoiceProfile` 可带 `library_id`（固定音色库条目 ID）；服务器验证其 `voice_id` 与已锁定原 ID 相同。不一致返回 409，条目缺失返回 404，并停止而不重新设计。资源库只提供读取、已有试听及备份接口；导入已激活记录使用本地维护脚本，不提供远程创建、更新或删除入口。

`VoiceProfile.source: "recording"` 表示此角色仅复用/导入录音，音频生成任务在 TTS 前停止并报 `RECORDED_AUDIO_ONLY`。未设置时沿用已有 TTS 方式；MiniMax 只允许系统目录已知音色和资源库已激活音色，其余在提交前报 `VOICE_POLICY_BLOCKED`。`GET /api/audio/voice-library` 的 `policy` 为只读费用规则；不提供启用新定制音色的开关。免音色费不等于免 TTS 字符费。

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
| `PUT /api/projects/{id}/audio-cast` | `{voices, speakers, expected_version}`；角色声线及逐镜说话者，修改使相关音轨与成片失效 |
| `POST /api/projects/{id}/jobs` | 异步步骤：`{stage, entity_id, force, reference_source_id?}`；后者仅用于参考图重生 |
| `PUT /api/projects/{id}/shots/{shot_id}/references` | 保存逐镜额外已批准素材参考和已批准前镜：`{artifact_ids, previous_shot_id, expected_version}`；改变依赖会使下游失效 |
| `GET /api/jobs/{id}/image-attempts/{number}` | 读取该任务已生成的候选图，包括尚未完成初检的候选；不代表素材已批准 |
| `POST /api/projects/{id}/automation` | 开关自动推进：`{enabled}` |
| `POST /api/projects/{id}/pause`、`resume`、`cancel` | 项目控制；暂停不杀供应商调用 |
| `POST /api/projects/{id}/upload` | multipart：`stage`、`entity_id`、`file`；导入待审核素材。用户上传的 `reference` 可在规范/蓝图批准前导入，但仍不能审核或触发下游生成 |
| `GET /api/projects/{id}/export.zip` | 原文、规范、计划、请求快照、结果资料 |
| `GET /api/jobs`、`GET /api/jobs/{id}` | 队列、日志、完整提示词与远端 ID |
| `POST /api/jobs/{id}/cancel`、`retry` | 取消 / 继续查询 / 显式重试 |
| `GET /api/artifacts` | 所有项目素材及过期状态 |
| `GET /api/artifacts/{id}/content` | 图片、视频、音频原始文件 |
| `GET /api/artifacts/{id}/thumbnail` | 历史图片的小尺寸预览 |
| `GET /api/storage`、`PUT /api/storage` | COS 配置（凭据不回显）、迁移数量与本地副本统计 |
| `POST /api/storage/test` | 小对象上传/读回与生命周期规则只读检查 |
| `POST /api/artifacts/{id}/review` | `{approved, note}`；旧版本不可批准 |
| `PUT /api/artifacts/{id}/reference-role` | 分类当前版本参考图，变更分类后须重新人工审核，已有下游审核失效 |
| `GET/PUT /api/settings` | 模型渠道、付费开关、声音与字幕 |
| `GET /api/audio/minimax/voices` | 使用已保存的 Key 读取账号可用音色，不生成语音 |
| `GET /api/audio/voice-library` | 本地只读、全局已锁定音色库；返回原 ID、参数、激活状态和试听地址，不调用供应商 |
| `GET /api/audio/voice-library/{id}/preview` | 校验 SHA-256 后播放已保存试听，不生成音频 |
| `GET /api/audio/voice-library/export.zip` | 私有音色记录、回执、生产来源与试听备份，不含 API Key |
| `GET /api/gateway/catalog` | 上游当前连接器目录 |
| `/api/gateway/accounts`、`web-image-providers`、`video-providers` | 受限制的本机账号管理转发 |
| `GET /api/upstreams` | 上游固定版本 |

参考素材上传可传 `reference_role`（人物 `front`、`side`、`full_body`、`costume`、`accessory`；场景 `wide`、`reverse`、`spatial`；道具/产品 `detail`、`held`；默认 `general`）。路由 `qc_max_cost_cny` 是用户填写的单次最高预计费用，`image_qc_budget_cny` 是每个图片任务的预计预算，默认 0；可能付费的图片/视觉请求会先预留预算，不代表实际账单上限。

Gemini 账号通过 `POST /api/gateway/accounts` 添加，`PUT /api/gateway/accounts/{id}` 更新名称、凭据或启停状态，`POST /api/gateway/accounts/{id}/connect` 分别检查连接。账号目录返回每个账号的 `enabled` 和 `state`，不返回 Cookie；当前网关最多保存 8 个，停用不删除凭据。其他连接器目前各保存一套凭据。网关按添加顺序优先使用已启用账号，额度不足或部分超时可能切换账号；超时切换可能重复请求，需核对网页记录。

`GET/PUT/DELETE /api/gateway/seedream` 直接同步网关配置；PUT 接收 `api_key`，返回值仅包含配置状态、模型、固定服务地址和参考价格，不返回密钥，密钥只存网关。已配置不表示已验证。网关原生兜底针对至少两个启用的 Gemini 账号本次均确认额度耗尽；工作台另支持所有启用账号（至少两个）均认证失效时兜底，仅对任务快照允许付费的图片请求生效。超时、其他错误或结果不明确不会触发认证兜底。工作台关闭付费时已配置网关兜底会拒绝 Gemini 图片请求；成功兜底记录实际模型及付费属性，Seedream 失败需先核对账单与供应商记录。

阶段 `stage`：`requirements`、`plan`、`reference`、`keyframe`、`video`、`audio`、`render`。模型生成的图片/视频阶段要求规范与分镜批准；用户上传的 `reference` 是唯一例外，只要已有匹配当前规范的分镜即可导入，但必须等待规范和分镜批准后审核；关键帧要求引用素材批准；视频要求关键帧批准；合成要求全部输入视频及独立配音批准。

重生 `reference` 可通过 `reference_source_id` 绑定同项目、同实体、当前规范与分镜版本下已批准的参考图。不指定时，自动选用当前版本该实体最近上传的原图；如果它尚未批准，或者上传原图仅属于旧版本，生成会被拒绝而不会退回文字生图。没有上传原图时仍可从文字创建素材。来源素材 ID 记录在任务快照及新素材 `dependencies` 中，实际文件会上传至模型渠道；原图和审核记录不变，新图为待审核。即使新图成为当前候选，后续重生仍默认以原上传图为依据。提交或接收结果时若候选、来源审核或版本已变化，任务不会覆盖当前素材。

项目创建字段包括 `title`、`mode`、`source`、`brief`、`episodes`（1–50）、`duration`（每集5–600秒）、`aspect_ratio`、`language`、`style`。分镜计划 Schema 见 OpenAPI；所有素材、集与镜头 ID 全局唯一；各集时长精确匹配，来源章节全覆盖。

任务状态：`queued`、`running`、`succeeded`、`blocked`、`failed`、`cancelled`。素材审核：`pending`、`approved`、`rejected`；`stale` 是相对当前规范、分镜和引用版本计算的状态。执行成功不自动批准。

图片和视频素材的 `route` 记录成功使用的 `provider`、`model`、`transport` 和 `paid`；Seedream 兜底使用网关实际返回的供应商与模型，而非原先请求的 Gemini。模型字段为渠道记录值，不表示工作台独立验证过供应商内部模型版本。上传、历史导入或缺少来源的素材不能据此推断生成渠道或费用。

工作台批量审核逐项调用 `POST /api/artifacts/{id}/review`，提交 `approved`、可选 `note` 和 `expected_review: "pending"`。服务端仍逐项校验规范、分镜及当前素材，并在审核状态已改变时返回 409；不会覆盖已审核结果。批量操作可部分成功，客户端分别展示成功数量和失败原因；未传 `expected_review` 的单项审核维持既有行为。

项目详情的 `automation_status` 描述自动推进：`disabled`、`paused`、`cancelled`、`scheduling`、`queued`、`running`、`attention`、`error`、`review`、`rework` 或 `completed`；可能附带当前 `stage`、需处理的 `job_ids` 或安全的调度错误。运行任务附带最近的 `latest_event`，完整事件仍通过任务详情读取。工作线程是否运行以健康接口的 `worker` 为准。当前阶段的任务失败、阻塞或取消后不会因同批其他任务成功而自动重提；需要用户明确重试。调度异常会暂停自动安排，`automation` 设置 `enabled: true` 可清除异常并重试。任务列表的 `verification_required` 标记需要核对的提交结果，不暴露运行时数据；工作台禁止批量重提此类任务，并在单独重新生成前要求核对与确认。

显式重试带网关 ID 的任务会先读取该网关任务：运行中或成功时沿用原任务继续查询和取回结果；确认失败或取消且结果明确时创建带 `retry_of` 的新任务和新的幂等键，保留旧记录及同一快照已完成的文字调用。重复重试同一轮会复用已有排队、运行或成功的重试。网关状态不可确认、超时或其他可能已提交的结果不会新建请求，须先核对供应商记录。项目详情和任务列表的 `verification_required` 都会标记此类风险。

本机历史迁移另有只读任务阶段 `archive`。素材的 `archived`、`title`、`thumbnail_url` 与 `metadata.imported` 用于历史浏览，原始审核结论单独保存，不允许通过审核接口将档案直接批准为生产素材。项目 `import_summary` 记录数量、来源分组和文件校验异常；迁移步骤见 [IMPORT.md](IMPORT.md)。素材的 `metadata.curation` 记录当前用途、选择依据和分集镜号，项目 `library_curation` 与 `reference_catalog` 提供用途计数及分集缺口。已整理项目的默认 ZIP 包含 `reference` 选定参考、`candidate` 待复核候选与当前制作资料，原审核状态保留；排除明确返修、旧试片和历史文件。`include_history=true` 显式包含完整历史档案。未整理项目保留完整迁移导出。使用磁盘临时文件响应。

通用应用错误：`{error:{code,message}}`；404 为缺失，409 为版本冲突，422 为输入或前置条件问题，503 为供应商/服务不可用。HTTP Schema 校验错误由 FastAPI 的 `detail` 返回。供应商原始鉴权响应不会写入工作台错误正文。所有运行数据与凭据禁止提交 Git。

COS 素材 `content` / `thumbnail` 在启用后返回 307，目标是短时签名的官方 COS HTTPS 地址；本地索引仅保存对象位置与校验信息。存储错误返回 503 `COS_STORAGE_ERROR`，不回显 SDK 原始请求、签名或凭据。见 [CLOUD_STORAGE.md](CLOUD_STORAGE.md)。
