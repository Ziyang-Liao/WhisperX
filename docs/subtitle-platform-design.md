# 视频字幕平台 —— 设计文档

> 状态:设计稿(2026-06-22)。本文档描述把现有「批量语音转文本平台」演进为「短视频多语言字幕平台」的目标架构与落地计划。
> 本轮只做**设计 + 修复现有阻断级 bug**,不部署任何 AWS 资源。

## 1. 目标与范围

### 要做的(需求)
- 上传**多个视频**,底层视频存放在 **S3**。
- 自动转写,生成**源语言字幕**;再翻译成用户选择的**多个目标语种**(多选)。
- 字幕产物为**软字幕 SRT/VTT**(不烧录进视频)。
- **视频列表**展示:每个视频的状态、各语言字幕的生成状态与下载入口。

### 明确不做的(避免过度设计)
- 不做硬字幕烧录(无 ffmpeg 重编码出片)。
- 不做「同一条视频内部混合语种」的逐块语言检测(track A:每个视频按整体自动检测一种源语言)。
- 不做自动伸缩 / 分布式 worker(单台 GPU 机够用;扩展路径在 §11 留有接口)。
- 暂不保留定时调度(scheduler)——本产品是按需触发,scheduler 是上一形态的遗留(见 §6)。

### 关键决策(已拍板)
| 项 | 决策 |
|---|---|
| 翻译引擎 | **Claude 大模型**(逐段翻译,保留源时间戳) |
| Claude 调用路径 | **Amazon Bedrock**(`anthropic.claude-opus-4-8`),花费/权限留在 temp-account 内 |
| 字幕形式 | **软字幕 SRT + VTT** |
| 源语言 | **每个视频自动检测**(track A),并提供手动指定入口 |
| 存储 | 原视频 + 字幕产物均存 **S3** |
| 部署账号 | **仅** `aws --profile temp-account`,不碰本机/默认 AWS 环境 |

## 2. 现状盘点

现有代码是一个可用的**纯音频、单语言、本地磁盘、SQLite** 批量转写平台:

- 后端 FastAPI:`audio`(增删改查 + 转写结果)、`transcription`(批量任务触发/查询)。
- `TranscriptionEngine`:WhisperX(large-v3)转写 + wav2vec2 词级对齐 + 可选 pyannote 说话人分离。
- `AudioManager`:本地 `data/uploads` 落盘、mutagen 取时长、分页/搜索。
- `BatchProcessor` + `TranscriptionScheduler`:互斥的批量处理 + cron 调度。
- 前端 React/Vite:音频列表、详情(播放器 + 转写结果)、任务监控页。

**与目标的差距**:无视频支持、无 S3、无翻译、无 SRT/VTT、无多语言、转写在请求内同步执行、引擎每请求重载。详见 §12 阻断级 bug。

## 3. 目标架构

推荐形态:**单台 GPU EC2 一体机 + S3**,API 与后台 worker 同进程。

```
                 ┌─────────────────────────── EC2 (g5/g6, temp-account) ───────────────────────────┐
   浏览器 ──────▶ │  FastAPI (API 层)                                                                │
   (前端)        │    │  enqueue: 写 DB 状态 (pending)                                                │
                 │    ▼                                                                              │
                 │  后台 worker 线程 (单实例, 持有 GPU 模型)                                          │
                 │    1) 转写: 拉 pending media → ffmpeg 抽音轨 → WhisperX 转写+对齐 → 源字幕         │
                 │    2) 翻译: 拉 pending 字幕轨 → Claude(Bedrock) 逐段翻译 → 生成 SRT/VTT            │
                 │    ▲                          │                          │                        │
                 │    │ 元数据/状态              │ 取/存视频、存字幕         │ InvokeModel             │
                 └────┼──────────────────────────┼──────────────────────────┼────────────────────────┘
                      ▼                          ▼                          ▼
                  SQLite/RDS                Amazon S3                 Amazon Bedrock
              (media/字幕轨元数据)     (原视频 + SRT/VTT)        (anthropic.claude-opus-4-8)
```

- **API 层**:接收上传、列表、触发生成、查询状态、下发下载链接。**绝不在请求里跑转写/翻译**(改掉现状的同步执行)。
- **后台 worker**:单线程守护进程,进程启动时加载一次 GPU 模型(改掉每请求重载);按「状态驱动队列」依次处理转写、翻译两个阶段。
- **S3**:原视频与字幕产物;前端通过**预签名 URL** 直传/直下,避免大文件经过后端内存。
- **Bedrock**:翻译。账单与 IAM 都在 temp-account 内。

> 为什么是单进程 worker 而不是 SQS:短视频低并发,单台 GPU 一次也只能跑一个转写,引入 SQS/独立 worker 是过度设计。§11 给出扩展路径。

## 4. 数据模型

由音频单语言模型演进为「媒体 + 多语言字幕轨」:

### `media_files`(由 `audio_files` 演进)
| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| filename | str | 原始文件名 |
| media_type | str | `video` / `audio` |
| s3_key | str | 原视频在 S3 的 key(替代 `stored_path`) |
| file_size | int | |
| duration | float | 秒 |
| format | str | mp4/mov/... |
| thumbnail_s3_key | str? | 可选缩略图 |
| source_language | str? | 检测或手动指定的源语种 |
| transcription_status | str | pending/processing/completed/failed |
| transcript_json | text? | 源语言带时间戳转写结果(SubtitleTrack 翻译的输入) |
| error_message | text? | |
| upload_time / created_at / updated_at | datetime | |

### `subtitle_tracks`(新增)
一个视频对应 N 条字幕轨(含源语言一条 + 每个目标语种一条)。
| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| media_file_id | int FK | |
| language | str | 该轨的语种(如 `ja`、`en`) |
| is_source | bool | 源语言轨为 true(无需翻译,直接由转写产生) |
| status | str | pending/processing/completed/failed |
| srt_s3_key | str? | 生成后的 SRT key |
| vtt_s3_key | str? | 生成后的 VTT key |
| error_message | text? | |
| created_at / updated_at | datetime | |
| | | **唯一约束** (media_file_id, language) |

> 状态列即「队列」:worker 扫 `transcription_status=pending` 的 media 做转写,扫 `status=pending` 的 track 做翻译。UI 与监控直接读这些状态,无需独立 Job 表(保持简单)。

**迁移**:引入 **Alembic** 管理 schema(现状只有 `create_all`,加列不会自动迁移)。因尚无生产数据,Phase 1 可直接以新 schema 起库。

## 5. API 设计

> 命名从 `audio` 收敛到 `media`。大文件走预签名直传,避免后端内存爆掉。

| 方法 & 路径 | 说明 |
|---|---|
| `POST /api/media` | 创建媒体记录,返回 `media_id` + **S3 预签名 PUT URL**(客户端直传) |
| `POST /api/media/{id}/complete` | 客户端直传完成后回调:校验 S3 对象、ffprobe 取时长/格式、置 `pending` |
| `GET /api/media` | 分页列表,每项含 media 信息 + 各字幕轨(语种+状态)汇总 + 缩略图预签名 URL |
| `GET /api/media/{id}` | 详情 |
| `GET /api/media/{id}/stream` | 返回视频播放用的预签名 GET URL(短时效) |
| `DELETE /api/media/{id}` | 删除 S3 视频 + 所有字幕 + DB 记录(处理中拒绝) |
| `POST /api/media/{id}/subtitles` | body:`{target_languages:[...]}`;为各语种建 `pending` 字幕轨并触发处理;源语言==目标则跳过翻译 |
| `GET /api/media/{id}/subtitles` | 列出各轨状态 + SRT/VTT 下载(预签名)URL |
| `GET /api/media/{id}/subtitles/{lang}.srt` / `.vtt` | 下载(预签名重定向或代理) |
| `POST /api/subtitles/generate` | 批量:`{media_ids:[...], target_languages:[...]}` 一次给多个视频排多语种 |

> 小文件可保留 `POST /api/media/upload`(multipart)作为兜底,但默认推荐预签名直传。

## 6. 处理流水线

后台 worker 的两个阶段(状态驱动,互不阻塞):

**阶段一 · 转写(GPU)**
1. 取 `transcription_status=pending` 的 media。
2. 从 S3 下载视频到本地临时文件。
3. `ffmpeg` 抽 16kHz 单声道音轨(WhisperX 输入)。
4. WhisperX:**自动检测语言** → 转写 → wav2vec2 词级对齐(+可选 pyannote 分离)。
5. 写 `transcript_json`、`source_language`;为源语种建/置一条 `is_source=true` 的字幕轨,生成其 SRT/VTT 传 S3;media 置 `completed`。

**阶段二 · 翻译(Bedrock)**
1. 取 `status=pending` 且其 media 已转写完成、`is_source=false` 的字幕轨。
2. 取该 media 的 `transcript_json` 的 N 个**带时间戳分段**。
3. 调 Claude(Bedrock)**逐段翻译**到该轨语种(见 §7)。
4. **复用源分段的 start/end**,把译文逐段填回,生成 SRT/VTT,传 S3;轨置 `completed`。

> 因为「复用源时间戳、只替换文本」,**时间轴永远对齐**,与翻译无关。
> **scheduler 处置**:本产品按需触发,建议移除 `TranscriptionScheduler`(YAGNI);如要保留定时批量,留作可选开关。

## 7. 翻译设计(Claude via Bedrock)

- **客户端**:`AnthropicBedrockMantle(aws_region=...)`,模型 `anthropic.claude-opus-4-8`。
- **分段对齐(关键)**:把源分段作为**带索引的结构化数组**发送,用**结构化输出**(`output_config.format` + JSON schema)强制返回**恰好 N 条**译文,逐条对应索引。Bedrock 支持结构化输出。
- **分块**:长视频按每块约 40–60 段切分,逐块翻译;**代码侧校验 `len(译文)==len(源)`**,不一致则对该块重试(降批量),仍失败则标记该轨 failed 并记录原因。
- **系统提示**:放风格/语气/术语表 + 目标语种说明;用 **manual `cache_control`** 缓存(Bedrock 无自动缓存,需手动打断点;多语种/多视频复用,省输入成本)。
- **thinking**:翻译是机械任务,**关闭 thinking**(省延迟/成本);因输出受 JSON schema 约束,不会有推理泄漏到结果。
- **模型分级(可选,后续按量调)**:常规语种可走 `claude-sonnet-4-6` 压成本,质量敏感语种走 Opus;先用 Opus 打底。
- **跳过**:目标语种 == 源语种时不翻译,直接复用源字幕轨。

抽象为 `TranslationEngine` 接口(与 `TranscriptionEngine` 并列),便于将来切换实现/模型。

## 8. 字幕生成(SRT/VTT)

由 `transcript_json` / 译文 + 源时间戳直接序列化:

```
SRT:                                   VTT:
1                                      WEBVTT
00:00:00,000 --> 00:00:02,500
译文第一段                              00:00:00.000 --> 00:00:02.500
                                       译文第一段
2
00:00:02,500 --> 00:00:05,000          00:00:02.500 --> 00:00:05.000
译文第二段                              译文第二段
```

- 软字幕,播放器自动换行,**无需根据译文长度重新计时**(保持简单)。
- 时间戳格式:SRT 用 `,` 毫秒分隔,VTT 用 `.`。
- 前端 `<video>` 用 `<track kind="subtitles" srclang=... src=.../{lang}.vtt>` 多语切换(VTT 浏览器原生支持;SRT 主要用于下载/上传到平台)。

## 9. S3 布局

```
s3://<temp-account-bucket>/
  videos/{media_id}/source.{ext}      # 原视频
  subtitles/{media_id}/{lang}.srt
  subtitles/{media_id}/{lang}.vtt
  thumbnails/{media_id}.jpg           # 可选
```
- 桶**私有**,关闭公共访问;一律预签名 URL 直传/直下。
- 删除 media 时按前缀清理 `videos/{id}/`、`subtitles/{id}/`、缩略图。
- S3 访问层抽象为 `StorageService`,本地可用 **moto/localstack** 做单测,**不碰真实 AWS**。

## 10. IAM 与安全(部署时,仅 temp-account)

- **EC2 实例角色**(最小权限):
  - S3:`GetObject/PutObject/DeleteObject/ListBucket` 限定到该桶。
  - Bedrock:`bedrock:InvokeModel` 限定到 Anthropic 模型 ARN。
  - CloudWatch Logs:写日志。
- **前置**:在 temp-account 的目标 region 开通 **Bedrock 对 Anthropic 模型的访问**。
- **鉴权(上线前必做)**:现状无鉴权且 CORS 为 `*`+credentials(非法组合)。公网前至少加 API Key/Token 或 Cognito,收紧 CORS。列入 Phase 4 必办项。
- 预签名 URL 设短时效(如 15 分钟)。

## 11. 扩展路径(暂不实现,留接口)

- 并发变大:把后台 worker 拆为独立进程 + **SQS** 队列,GPU worker 独立伸缩;`StorageService`/`TranslationEngine`/状态机不变。
- 多 worker / 多实例:SQLite → **RDS Postgres**(状态列加乐观锁或 `SELECT ... FOR UPDATE` 领取任务)。
- 成本:翻译接 Bedrock 后,常规语种降级 Sonnet;转写按 GPU 机型(g4dn/g5/g6,见仓库 benchmark)选性价比。

## 12. 现有代码的阻断级 bug(本轮就地修复)

1. **播放器 404**:前端引用 `/api/audio/{id}/file`,后端无此路由 → 加文件服务端点(本地阶段)。
2. **每请求重载模型**:`transcription.py` 的 `_get_processor` 每次 new `TranscriptionEngine`,重载 large-v3 → 改为模块级**单例**,模型只加载一次。
3. **锁形同虚设 + TOCTOU**:`BatchProcessor._lock` 随每请求新建,无法跨请求互斥;并发触发存在检查-写入竞态 → 用**模块级锁**真正串行化触发。

> 这三个是当前代码确定性坏掉/生产必炸的点,先修。其余(同步执行转写、上传读进内存、无鉴权、搜索匹配原始 JSON 串、诊断未接线、无迁移等)属于重构范畴,在 Phase 1–2 随架构改造一并处理,不在本轮。

## 13. 分阶段落地

- **Phase 0(本轮)**:本设计文档 + 修复 §12 三个阻断级 bug。**不碰 AWS。**
- **Phase 1 · 存储与异步**:`StorageService`(S3,moto 本地测)、`MediaFile`/`SubtitleTrack` 模型 + Alembic、视频上传(预签名)+ ffmpeg 抽音轨、**后台 worker**(模型单例)、转写改异步、移除 scheduler。
- **Phase 2 · 翻译与字幕**:`TranslationEngine`(Bedrock,结构化输出 + 分段对齐 + 计数校验)、SRT/VTT 生成、多语言贯通、目标==源跳过。
- **Phase 3 · 前端**:视频上传 UI、**语种多选**、视频列表(各语言字幕状态 + 下载)、`<video>` + 多语 `<track>` 预览。
- **Phase 4 · 部署 temp-account**:GPU EC2 + S3 + IAM + 开通 Bedrock;加鉴权、收紧 CORS;端到端验证。**动手前先报资源清单与预估费用。**

## 14. 待确认(进入 Phase 4 前)
- 部署拓扑确认(默认单台 GPU EC2 一体机)与机型(g5/g6)。
- 鉴权方式(API Key / Cognito / 其他)。
- 目标语种清单与是否启用模型分级(Opus 打底 vs 常规走 Sonnet)。
- 预计并发与视频规模(决定是否需要走 §11 扩展路径)。
