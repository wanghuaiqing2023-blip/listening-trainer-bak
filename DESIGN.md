# 听力训练系统 — 技术设计文档

## 1. 系统概述

本系统是一套基于**可理解输入 i+1 原则**的自适应英语听力训练系统。
核心理念：只对用户当前能力或能力+1级别的音频片段进行训练，跳过超出范围的内容，通过四道客观关卡验证真正掌握。

---

## 2. 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 后端框架 | Python 3.11 + FastAPI | 异步 REST API |
| 数据库 | SQLite + SQLAlchemy ORM | 本地持久化 |
| STT + 音素对齐 | WhisperX | 本地运行，词级+音素级时间戳 |
| 语音评估 | Azure Speech SDK | 音素级发音评估 |
| TTS | Azure Neural TTS (en-US-JennyNeural) | 泛化测试朗读 |
| 大模型 | OpenAI GPT-4o | 泛化测试句子生成、评估听写 |
| 音频处理 | ffmpeg + pydub + librosa | 提取、切片、变速、加噪 |
| NLP | spaCy (en_core_web_sm) + wordfreq | 句法分析、词频查询 |
| SRS | 自实现 SM-2 | 间隔重复记忆算法 |
| YouTube | yt-dlp | 视频下载 |
| 前端 | Vue 3 + Vite + Pinia | 响应式 SPA |
| 波形显示 | WaveSurfer.js v7 | 音频波形可视化播放器 |

---

## 3. 核心流程

### 3.1 内容处理流水线

```
用户输入
  ├─ 上传文件（MP3/MP4/WAV/MKV）
  └─ YouTube URL → yt-dlp 下载

↓ ffmpeg 提取为 16kHz 单声道 WAV

↓ WhisperX 转录
  ├─ 文字转录（sentence-level）
  └─ 词级时间戳对齐（每个词的 start/end）

↓ 句子+气口切割（segmenter.py）
  ├─ 检测词间停顿 > 0.4s → 切割
  ├─ 句子时长 > 15s → 强制切割
  └─ 时长 < 1.5s → 合并

↓ 每个片段并行处理：
  ├─ ffmpeg 切割片段音频
  ├─ Azure Speech 音素评估 → 语音现象检测
  └─ 五维度难度评分

↓ 写入数据库（segments 表）
↓ 提取词汇写入 vocabulary 表
↓ content.status = "ready"
```

### 3.2 语音现象检测（音频实测，非文字推断）

**原则**：不基于文字规则猜测，而是通过对比实际发音音素与字典标准音素来检测音变。

```
WhisperX → 每个词的时间戳
Azure Speech Pronunciation Assessment → 每个词的实际发音音素序列
CMU发音字典（WEAK_FORMS）→ 标准音素序列

实际音素 vs 标准音素 → 差异 = 检测到的音变
```

| 现象 | 检测逻辑 |
|---|---|
| 弱读 | 实际音素与弱读形式相似度 > 与强读形式相似度 |
| 闪音 | Azure 返回音素中出现 DX（ARPAbet flap） |
| 省略 | 实际音素数量明显少于标准序列 |
| 同化 | 相邻词边界 prev_last + curr_first 匹配同化规则表 |
| 连读 | 相邻词边界时间间隔 < 30ms（无停顿融合） |

### 3.3 难度评分（五维度，取最大值）

每个维度独立打分 1-10，**最难维度决定总分**（短板原则）。

| 维度 | 计算方法 | 1分参考 | 10分参考 |
|---|---|---|---|
| 语速 | `WPM = 词数 / 时长 * 60`，线性映射 | 100 WPM | 200 WPM |
| 音变密度 | `音变数 / 总词数 * 20` | 几乎无音变 | 每词都有音变 |
| 词汇难度 | `1 - 用户掌握概率`（加权平均） | 全是已掌握词 | 全是陌生低频词 |
| 句法复杂度 | `依存树最大深度 * 1.2 + 从句数 * 0.8` | 简单短句 | 多层嵌套从句 |
| 音频质量 | `10 - SNR(dB) / 3`（librosa计算） | 录音室质量 | 嘈杂街头录音 |

**词汇难度的个性化**：
- 已知词：P(mastery) 高 → 难度贡献低
- 未知词：P(mastery) 低 → 难度贡献高
- 从未见过的词：用 wordfreq 词频作先验估计 P₀

### 3.4 i+1 筛选

```python
# 只展示在用户能力范围 [i, i+1] 内的卡片
if level_score <= card.diff_total <= level_score + 1.0:
    show_card()
else:
    skip()
```

用户水平动态更新：随着词汇掌握概率上升，同一张卡片的词汇难度分自动下降，以前被 skip 的卡片会逐渐进入训练队列。

### 3.5 词汇贝叶斯追踪

每个词维护一个掌握概率 P ∈ [0, 1]，使用 Beta 分布更新：

```
α = P * (α₀ + β₀)
β = (1-P) * (α₀ + β₀)

答对: α += difficulty_weight
答错: β += difficulty_weight

P_new = α / (α + β)
```

**时间衰减（Ebbinghaus 遗忘曲线）**：
```
P_decayed = prior + (P - prior) * exp(-days_elapsed / 30 * ln2)
```

颜色划分：
- P < 0.30 → 蓝色（未知）
- 0.30 ≤ P < 0.85 → 黄色（学习中）
- P ≥ 0.85 → 白色（已掌握）

### 3.6 掌握验证四关卡

#### Gate 1: Shadowing Match（跟读验证）
- 用户按住麦克风模仿音频
- Azure Speech Pronunciation Assessment 评估音素级匹配率
- 发音评分 ≥ 90 → pass；连续 3 次 pass → Gate 1 通过

#### Gate 2: Generalization Test（泛化测试）
- GPT-4o 根据卡片中检测到的音变规律，生成**全新句子**
- Azure Neural TTS 朗读（用户不看文字）
- 用户听写，GPT 评估是否正确（容忍合理的语音简化形式）
- 通过 → Gate 2 完成

#### Gate 3: Stress Test（压力测试）
- 随机施加干扰：加白噪音（SNR 10dB）或 1.5x 语速
- 用户在干扰条件下听写
- 准确率 ≥ 85% → Gate 3 通过

#### Gate 4: SRS 时间检验
- SM-2 算法：1d → 6d → 间隔 * ease_factor → ... → 90d
- 90天后复习仍然通过 → state = mastered（真正掌握）

### 3.7 SRS（SM-2）算法

```python
if quality < 3:              # 答错
    new_interval = 1
    ease -= 0.2

elif state == "new":         # 首次通过
    new_interval = 1

elif interval == 1:          # 第二次通过
    new_interval = 6

else:                        # 后续复习
    new_interval = round(interval * ease)

ease += 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
ease = max(1.3, ease)
```

---

## 4. 数据库设计

### users
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT | |
| level_score | FLOAT | 当前 i 值（1-10） |
| created_at | DATETIME | |

### contents
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| user_id | FK | |
| title | TEXT | |
| source_type | TEXT | file / youtube |
| source_path | TEXT | |
| audio_path | TEXT | 提取后的 WAV 路径 |
| status | TEXT | processing / ready / error |
| error_msg | TEXT | |

### segments（核心表）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| content_id | FK | |
| index | INTEGER | 片段序号 |
| text | TEXT | WhisperX 转录文字 |
| start_time | FLOAT | 秒 |
| end_time | FLOAT | 秒 |
| audio_path | TEXT | 切片音频文件 |
| diff_speech_rate | FLOAT | 语速维度得分 |
| diff_phonetics | FLOAT | 音变密度维度得分 |
| diff_vocabulary | FLOAT | 词汇难度维度得分（客观值，动态重算） |
| diff_complexity | FLOAT | 句法复杂度维度得分 |
| diff_audio_quality | FLOAT | 音频质量维度得分 |
| diff_total | FLOAT | 总难度（客观最大值） |
| phonetic_annotations | JSON | 音变标注列表 |
| word_timestamps | JSON | WhisperX 词级时间戳 |

### user_cards（SRS 进度）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| user_id | FK | |
| segment_id | FK | |
| state | TEXT | new / learning / review / mastered |
| interval_days | INTEGER | SM-2 当前间隔 |
| ease_factor | FLOAT | SM-2 难度系数 |
| next_review | DATETIME | |
| shadow_streak | INTEGER | 连续跟读通过次数（需≥3） |
| gen_passed | BOOLEAN | |
| stress_passed | BOOLEAN | |

### vocabulary（词汇追踪）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| user_id | FK | |
| word | TEXT | |
| mastery_prob | FLOAT | 贝叶斯掌握概率 0-1 |
| encounters | INTEGER | 遇到次数 |
| correct_count | INTEGER | 答对次数 |
| last_seen | DATETIME | 用于遗忘衰减 |

---

## 5. API 设计

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /content/upload | 上传文件 |
| POST | /content/youtube | 提交 YouTube URL |
| GET | /content/{id}/status | 轮询处理状态 |
| GET | /content/ | 内容列表 |
| GET | /cards/ | i+1 筛选后的卡片列表 |
| GET | /cards/{id} | 单张卡片详情 |
| GET | /audio/{segment_id} | 片段音频流 |
| GET | /audio/tts/{filename} | TTS 音频流 |
| GET | /audio/stress/{filename} | 压力测试音频流 |
| POST | /mastery/shadow | 提交跟读音频（Azure评估） |
| POST | /mastery/generalize/generate | 生成泛化测试句子+TTS |
| POST | /mastery/generalize/submit | 提交泛化听写答案 |
| POST | /mastery/stress/generate | 生成压力测试音频 |
| POST | /mastery/stress/submit | 提交压力测试答案 |
| POST | /mastery/review | SRS 复习评分 |
| GET | /user/level | 获取用户水平 |
| PUT | /user/level | 设置用户水平 |
| GET | /user/test/sentences | 获取水平测试题目 |
| POST | /user/test/submit | 提交水平测试结果 |
| GET | /vocabulary/ | 词汇列表（含过滤） |
| GET | /vocabulary/stats | 词汇统计 |
| PUT | /vocabulary/{word} | 手动修改词汇掌握概率 |

---

## 6. 项目结构

```
listening-trainer/
├── backend/
│   ├── main.py              # FastAPI 入口，路由注册，音频 static 服务
│   ├── config.py            # 配置（API keys、路径、阈值）
│   ├── database.py          # SQLAlchemy 引擎和 session
│   ├── models.py            # ORM 模型（User/Content/Segment/UserCard/Vocabulary）
│   ├── routers/
│   │   ├── content.py       # 上传/YouTube/状态轮询
│   │   ├── cards.py         # 卡片列表（i+1筛选）和详情
│   │   ├── mastery.py       # 四关卡验证接口
│   │   ├── user.py          # 用户水平+水平测试
│   │   └── vocabulary.py    # 词汇列表和统计
│   ├── services/
│   │   ├── pipeline.py      # 处理流水线编排
│   │   ├── youtube.py       # yt-dlp 封装
│   │   ├── transcriber.py   # WhisperX 封装
│   │   ├── segmenter.py     # 气口切割算法
│   │   ├── difficulty.py    # 五维度难度评分
│   │   ├── phonetics.py     # 音素比对语音现象检测
│   │   ├── azure_speech.py  # Azure 发音评估 + TTS
│   │   ├── openai_service.py# GPT 句子生成+听写评估
│   │   ├── srs.py           # SM-2 算法
│   │   └── vocabulary.py    # 贝叶斯词汇追踪
│   └── utils/
│       ├── audio.py         # ffmpeg/librosa 工具
│       └── text.py          # 文本处理工具
├── frontend/
│   ├── src/
│   │   ├── views/
│   │   │   ├── Onboarding.vue   # 初始水平测试
│   │   │   ├── Upload.vue       # 上传/YouTube
│   │   │   ├── Library.vue      # 训练库（i+1卡片列表）
│   │   │   ├── Training.vue     # 训练卡片主界面
│   │   │   └── Vocabulary.vue   # 词汇仪表板
│   │   ├── components/
│   │   │   ├── AudioPlayer.vue       # WaveSurfer 播放器
│   │   │   ├── DifficultyBadge.vue   # 难度颜色徽章
│   │   │   ├── ShadowingRecorder.vue # 跟读录音+评估
│   │   │   ├── GeneralizationTest.vue# 泛化测试
│   │   │   └── StressTest.vue        # 压力测试
│   │   ├── stores/
│   │   │   ├── user.js
│   │   │   └── vocabulary.js
│   │   ├── api.js           # axios 封装
│   │   └── assets/main.css  # 全局样式（CSS变量+语音现象配色）
│   └── package.json
├── data/
│   ├── uploads/             # 原始上传文件
│   ├── audio_segments/      # 切割后的片段音频
│   └── db/listening.db      # SQLite 数据库
├── .env                     # API Keys（不提交到 git）
├── .env.example             # 配置模板
├── requirements.txt
├── start.sh                 # Linux/Mac 一键启动
├── start.bat                # Windows 一键启动
├── DESIGN.md                # 本文件
└── README.md                # 使用说明
```

---

## 7. 扩展方向

- 多用户支持（目前单用户模式）
- 用户水平自动动态调整（根据训练表现）
- 更精细的音素比对（引入完整 CMU Pronouncing Dictionary）
- 离线模式（本地 LLM 替代 GPT）
- 移动端 PWA 支持
