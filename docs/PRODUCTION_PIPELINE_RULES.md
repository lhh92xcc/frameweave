# 完整流水线规则文档（直接丢给 AI 作为系统提示 / 调度规则）

>
> 目标：AI 短剧自动化链路：【入参校验 → Vidu 视频生成（自动分流 Q2 / Q2-Pro / Q3）→ TTS 人声 → BGM + 音效混音 → ASS 字幕 → ASS 滚动弹幕 → FFmpeg 合成成片】
> 前提：后端已封装 TokenHub Vidu Client，接口路由全部共用一套，仅靠 model + 参数校验分支；不改动底层 http / 轮询 / 下载逻辑；Vidu 只负责画面，**声音、字幕、弹幕全部后置合成，不在 Vidu 内部完成**
> 模型枚举（TokenHub 标准 model_id，固定不变）
> Q2_GROUP = {"vidu-video-q2", "vidu-video-q2-turbo", "vidu-video-q2-pro"}
> Q3_GROUP = {"vidu-video-q3-pro", "vidu-video-q3-turbo"}

---

## 一、全局前置分流 & 参数校验规则（最高优先级，先执行）

### 分组定义

```
Q2普通组：vidu-video-q2、vidu-video-q2-turbo
Q2-Pro：vidu-video-q2-pro
Q3组：vidu-video-q3-pro、vidu-video-q3-turbo
```

### 自动分流判定逻辑（按顺序判断，命中即锁定 model，覆盖用户传入 model）

1. 如果请求携带 `subjects[*].videos`（视频参考素材）
   - ✅ 强制锁定 model = `vidu-video-q2-pro`
   - ❌ 禁止使用任何 Q3 模型，直接抛出校验错误
2. 如果请求开启 `audio: true`（Vidu 原生音画同步）
   - ✅ 强制锁定为 Q3 系列（默认 vidu-video-q3-turbo，可配置优先 pro）
   - ❌ Q2 全系不支持 audio/audio_type，参数拦截
3. 如果请求开启 `bgm: true`（Vidu 内置背景音乐）
   - ✅ 分配到 Q2 组（默认 vidu-video-q2-turbo，可切换 q2-pro/q2 标准版）
   - ❌ Q3 直接删除 bgm 字段，该参数无效
4. 如果 duration > 10 且 ≤16
   - ✅ 强制 Q3 组
   - ❌ Q2 最大时长 10s，超过直接报错
5. 其余无硬性约束场景（仅图片参考、1~10s、不开 audio、不开 bgm）
   - 优先使用全局默认 model（默认：vidu-video-q3-turbo），允许用户手动指定 model

### 分支参数白名单校验（锁定 model 后执行，非法参数剔除 / 报错）

#### 分支 A：model 属于【Q2 普通组：vidu-video-q2 /vidu-video-q2-turbo】

✅ 允许字段：model、prompt、duration (1~10)、aspect_ratio、resolution、seed、payload、images、subjects.images、bgm
❌ 禁止字段：audio、audio_type、subjects.videos

>
> 说明：不支持视频参考，不能传 subjects 里的 videos 数组

#### 分支 B：model = 【vidu-video-q2-pro】

✅ 允许字段：model、prompt、duration (1~10)、aspect_ratio、resolution、seed、payload、images、subjects.images、subjects.videos、bgm
❌ 禁止字段：audio、audio_type

>
> 唯一支持视频参考输入的模型

#### 分支 C：model 属于【Q3 组：vidu-video-q3-pro /vidu-video-q3-turbo】

✅ 允许字段：model、prompt、duration (1~16)、aspect_ratio、resolution、seed、payload、images、subjects.images、audio、audio_type
❌ 禁止字段：bgm、subjects.videos

>
> Q3 全部不支持视频参考输入，入参直接过滤 subjects.videos

>
> 统一限制：images /subjects 图片必须是公网 url 或者标准 base64；视频参考（仅 q2-pro）必须为公网 mp4，5s 以内，≤100MB

---

## 二、阶段 1：调用 TokenHub Vidu 生成画面视频

### 接口固定路由（全部模型共用）

基础域名：`https://tokenhub.tencentmaas.com/v1/wand/vidu/`

- 文生视频 POST `/text-to-video`
- 图生视频 POST `/image-to-video`
- 参考生视频 POST `/reference-to-video`
- 首尾帧过渡 POST `/start-end-to-video`
- 任务轮询查询 POST `/task/get`

鉴权头固定：`Authorization: Bearer {tokenhub_key}`，Content-Type: `application/json`

### 任务生命周期

1. 提交任务 → 返回 task_id
2. 轮询查询 task 状态：created /queueing/processing /success/failed
3. success 状态拿到 `video_url`（临时链接有效期 12 小时，立刻下载原始视频 raw.mp4）
4. failed：返回错误信息，终止流水线

>
> 输出产物：`raw.mp4`（纯画面，不带任何声音、字幕、弹幕）

>
> 重要业务约定：**不推荐依赖 Vidu 自带 audio/bgm 做成片声音，仅作为预览；精品短剧一律走后置 TTS 混音链路**
> 若用户开启 Vidu audio 模式：流水线依然保留后置混音，Vidu 原声轨道会被后置音频覆盖（可配置开关是否保留 vidu audio）

---

## 三、阶段 2：剧本台词 → TTS 人声生成（独立模块）

输入：台词时间轴数组（业务剧本输出，必须携带起止时间）

```
[
  {"start":0.0, "end":2.8, "text":"我从未想过事情会变成这样","speaker":"girl","speed":1.05},
  {"start":3.1, "end":6.0, "text":"但从这天起，我不会再退缩了","speaker":"boy","speed":1.0}
]
```

1. 调用 TTS 服务（火山 / 讯飞 / Azure，配置多角色音色）逐句生成单条人声音频
2. 按时间轴拼接人声轨道 `speech_track.mp3`
3. 人声预处理：音量标准化，峰值限制，避免爆音
输出产物：`speech_track.mp3`

---

## 四、阶段 3：BGM + 音效混音处理

输入：

- BGM 文件（预先素材库）
- 音效时间轴数组

```
[
  {"time":1.2, "sound_file":"whoosh.mp3", "volume":0.7},
  {"time":4.5, "sound_file":"sigh.mp3", "volume":0.5}
]
```

混音规则：

1. 无人声时间段：BGM 音量正常（-12db ~ -15db）
2. 人声存在时间段：BGM 自动压低到 -20db（自动闪避 duck）
3. 音效叠加到总音频轨道，按时间点延迟插入
输出产物：`mix_audio.mp3`（人声 + BGM + 全部音效合并完成音轨）

---

## 五、阶段 4：生成 ASS 字幕文件（底部台词字幕）

输入：同一份台词时间轴
字幕模板固定 ASS 样式（白底黑描边，短剧通用）

```
[Script Info]
Title:Sub
ScriptType: v4.00+
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Default,微软雅黑,30,&H00FFFFFF,&H000000,&H80000000,1,1,2,1,20,20,30
[Events]
```

每一条台词生成 Dialogue 行，对齐 start/end 时间
输出产物：`subtitle.ass`

---

## 六、阶段 5：生成滚动弹幕 ASS 文件（右往左滚动弹幕）

输入：弹幕时间轴数组

```
[
  {"start":1.5, "duration":5, "text":"来了来了！","color":"&H00FFFFFF&","y_pos":180},
  {"start":3.2, "duration":5, "text":"主角觉醒！","color":"&H00FFFF&","y_pos":260}
]
```

弹幕规则：

- 使用 ass move 标签实现从右向左滚动：`{\move(1920,%y%,-400,%y%)\1c%color%}`
- y_pos 自动分配轨道，防止弹幕重叠，同轨道弹幕错开时间
输出产物：`danmaku.ass`

---

## 七、阶段 6：FFmpeg 一次性合成最终成片

输入文件：
raw.mp4（Vidu 原始画面）、mix_audio.mp3（总音轨）、subtitle.ass、danmaku.ass
FFmpeg filter 逻辑：

1. 视频流叠加两层 ass：底层台词字幕 + 上层滚动弹幕
2. 替换原始音频为混音好的 mix_audio
3. 编码参数：h.264，aac 音频，可配置分辨率码率
示例命令模板（给 AI 直接调用）

```
ffmpeg -i raw.mp4 -i mix_audio.mp3 \
-filter_complex "[0:v]ass=subtitle.ass,ass=danmaku.ass[v];[1:a]anlmdn[a]" \
-map "[v]" -map "[a]" \
-c:v libx264 -crf 23 -preset medium -c:a aac -b:a 128k \
-y final_output.mp4
```

输出产物：`final_output.mp4` = 带画面 + 人声 + BGM + 音效 + 底部字幕 + 滚动弹幕的完整短剧成片

---

## 八、异常 & 兜底规则（AI 调度必须遵守）

1. Vidu 任务失败：直接终止流水线，返回错误，不执行后面 TTS / 合成
2. TTS 音频生成失败：可配置兜底（无音频版本视频，标记异常）
3. 弹幕数组为空：跳过 danmaku.ass 叠加，只压字幕
4. 无 BGM 素材：只保留人声 + 音效轨道
5. 时长校验兜底：所有阶段时间轴不能超过 Vidu 生成视频总时长，超出的台词 / 弹幕自动截断丢弃
6. 计费上报：credits 消耗、价格按 model 分组上报，Q2 普通 / Q2pro/Q3pro/Q3turbo 独立计费配置

## 九、给 AI 的执行顺序强制锁死（不能调换顺序）

```
入参校验 & 自动选择model
    ↓
Vidu提交画面生成任务 + 轮询下载 raw.mp4
    ↓
TTS生成人声轨道
    ↓
BGM+音效闪避混音 → mix_audio.mp3
    ↓
生成台词ass字幕
    ↓
生成滚动弹幕ass
    ↓
FFmpeg合成 final_output.mp4
```
