# 导入本机历史制作素材

历史素材可整体迁入工作台，保留原文件名、来源分组、镜头编号及原审核记录。导入只复制本机文件，不调用模型。导入项目默认关闭自动生产，规范与分镜等待复核；历史文件不会自动成为当前生产步骤的已批准输入。

## 准备清单

把清单保存在忽略提交的 `.local/imports/`，使用绝对 `source_root` 和相对路径，显式选择需要导入的目录和制作文档：

```json
{
  "format": "frameweave.archive.v1",
  "source_root": "/absolute/path/to/old-project",
  "project": {
    "title": "历史制作资料",
    "source": "完整原文或制作背景资料。"
  },
  "contract_markdown": "# 原制作规范\n规范完整内容。",
  "groups": [
    {"directory": "assets/characters", "label": "角色参考图"},
    {"directory": "content/selected-production", "label": "历史制作版"}
  ],
  "documents": ["content/selected-production/REVIEW.md"],
  "file_metadata": {
    "assets/characters/example.png": {
      "quality_status": "原记录状态",
      "production_approval": "PENDING",
      "recorded_sha256": "原记录中的可选哈希"
    }
  }
}
```

支持图片 PNG/JPG/JPEG/WebP/GIF/SVG、视频 MP4/MOV/WebM/M4V、音频 MP3/WAV/M4A/AAC/FLAC。制作文档需显式指定，支持 MD/TXT/JSON/SRT。不要选择包含凭据的文档。可附 `chapters` 和符合工作台 `ProductionPlan` Schema 的 `plan`；分镜需满足集数、每集总时长和章节覆盖要求。

## 执行

```bash
# 先盘点；不会复制或写入数据库
.venv/bin/python scripts/import_media.py .local/imports/source-manifest.json --dry-run

# 停止当前使用的启动方式，避免同时写数据库
docker --context orbstack compose down
# 原生运行时则使用 python3 scripts/dev.py stop

.venv/bin/python scripts/import_media.py .local/imports/source-manifest.json
docker --context orbstack compose up -d --build
```

导入前自动备份 SQLite。媒体按内容哈希去重保存，每个原路径保留独立素材条目；媒体、制作文档复制后逐一核验 SHA-256，全部完成后在单个数据库事务中发布项目。损坏媒体仍被保留并标记读取错误。相同清单及相同文件快照重复执行不会创建重复项目；源文件发生变化则创建新快照。中途失败时项目不会部分发布，已复制文件可能保留，可重新运行清单。

## 查找与使用

打开项目的 **浏览导入素材**，或访问 `/#assets/项目ID`。素材库支持按项目、来源分组、素材类型和文件名/镜头号/原路径筛选，每页 32 份；详情可预览原文件、下载、查看来源与原 QC 状态。原 QC 哈希与现文件不符时单独提示，不能把旧结论用于现文件。

历史素材只作档案浏览。需要继续生产时，先复核规范和分镜，再将选中的文件上传到当前指定素材或分镜，按正常流程审核。导入不会撤销原拒收结论或提升候选图审批。

项目 ZIP 包含迁移清单和制作文档，使用磁盘临时 ZIP 避免大型素材包全部驻留内存。逐文件来源、目标名和 SHA-256 存于 `.local/imports/项目ID/manifest.json`。备份、媒体、清单与制作文档均保持本机私有，不提交公开 Git 仓库。

## 按当前制作线整理

整目录导入用于保存历史，不能把导入数量当成可用素材数量。对照当前剧本、明确选定记录、实际生成输入及 SHA-256 建立私有选择清单，再停止服务执行：

```bash
.venv/bin/python scripts/curate_media.py .local/imports/curation-manifest.json
```

清单包含 `project_id`、说明 `note`、分集目录 `episodes` 和显式 `selections`。每个选择条目必须有 `source_path`、`sha256`、`state`、`label`；可记录 `episode`、`shot_id`、`display_title`、`reason`、`static_qc`、`evidence` 和 `order`。`state` 分为 `reference`（当前参考）、`candidate`（待复核）、`repair`（明确返修）、`test`（动作/技术试片）、`history`（历史排除）。未被选择的档案自动归入历史排除；可通过 `exclusions` 按原路径记录具体原因。选择先核验文件哈希，随后备份数据库和完整旧记录，再事务更新用途；重复同一清单保持幂等。

图库默认“当前素材”同时展示选定参考与待复核候选，并保留卡片状态。两者也可分别筛选；明确返修、旧版试片与历史排除需主动切换。待复核表示仍在当前制作线上，不能当成废弃，也不能当成已批准。集数选项显示已生成数量；选集后显示选定参考、待复核、返修与缺失数量，并可直接查看该集返修。原审核与制作批准不被修改；限范围静帧参考也不等于正式视频批准。人物和场景基准需有当前生成记录的实际引用依据，不能按文件名或修改时间就判断为可用。

如果原工作区引用了不同版本的剧本，可在清单中提供 `current_script`（`content`、`sha256`、`chapters`）。整理会保存旧工作区、换入当前原文，并清空不适用的旧分镜，等待重新规划与批准；不会编造新分镜、自动生成或发起付费调用。

已整理项目的默认 ZIP 包含选定参考、待复核候选、当前剧本及整理记录，`artifacts.json` 保留各自用途与原审核状态；不重新打包返修、旧版试片或废弃文件。只有显式请求 `/api/projects/项目ID/export.zip?include_history=true` 才导出完整历史。源文件与备份保持本机私有。
