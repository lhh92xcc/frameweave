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
