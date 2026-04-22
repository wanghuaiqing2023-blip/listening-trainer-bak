# Segment Review Pipeline Memo

## 1. 文档目的

本文档用于记录一个新的复查 pipeline 设计备忘录。

目标不是重复主流程，而是在 `segment` 生成后增加一道质检层，用来：

- 判断当前切片结果是否可信
- 找出失败原因，而不只是给出通过/不通过
- 为后续规则优化、失败样本修复、统计分析提供结构化依据

这份文档是备忘录，不是最终实现说明书。


## 2. 需要解决的问题

当前主流程已经可以完成：

- 下载 YouTube 视频
- 抽取音频
- WhisperX 转写
- 语义切句
- ASR 文本修正
- 音频切片
- 音系现象分析
- 难度计算
- 入库

但主流程目前缺少一层系统性的复查能力。

这会导致两个问题：

1. 系统可能把低质量 segment 直接入库。
2. 即使我们发现结果不好，也很难知道失败的真正原因。

因此，需要新增一个 `review pipeline`，在入库前对 segment 做一次复查。


## 3. 核心设计原则

### 3.1 复查不是重复主流程

复查层不负责重新完整生成 segment。
复查层负责判断：

- 这条 segment 是否可信
- 如果不可信，问题出在哪里


### 3.2 必须输出失败原因

复查结果不能只有：

- `pass`
- `fail`

而应该输出：

- 通过状态
- 风险分数
- 失败原因类型
- 对应证据
- 建议动作


### 3.3 优先做可解释的规则复查

第一版复查系统优先采用：

- 硬规则
- 对齐检查
- 差异比对

原因是这些结果稳定、可统计、可复现，也更适合成为后续迭代的基础。


### 3.4 复查层服务于主流程优化

复查系统的价值不只是拦截坏样本，更重要的是：

- 统计最常见失败模式
- 反推主流程中最脆弱的环节
- 让后续规则优化更有方向


## 4. 推荐插入位置

建议把复查层放在：

`transcribe -> segment -> asr_correct -> review -> explain / phonetics / save`

更完整一点可以是：

`transcribe -> segment -> asr_correct -> review -> repair(optional) -> explain / phonetics / save`

原因：

- 在 `asr_correct` 之后，文本已经成型
- 在 `save` 之前，失败样本还没有正式入库
- 这时已经有音频、词时间戳、句子文本、切片边界候选，足够做高质量复查


## 5. 复查层输入

每个 segment review 至少需要以下输入：

- `content.audio_path`
- `seg.text`
- `seg.start`
- `seg.end`
- `seg.words`
- 修正前文本
- 修正后文本
- WhisperX 原始词时间戳
- 主流程中的中间信号

其中一个关键点是：

当前 `segmenter.py` 的 `apply_asr_correction()` 会修改 `seg.text`，但不会同步修改 `seg.words` 和时间戳。

这意味着复查层必须特别检查：

- 修正后的文本是否仍然和原始音频片段一致
- 修正是否已经超出原始时间戳 span 的承载范围


## 6. 复查层输出

建议统一输出如下结构：

```json
{
  "passed": false,
  "score": 0.42,
  "reasons": [
    {
      "type": "correction_overreach",
      "severity": "high",
      "evidence": {
        "original_text": "I mean if you want to go",
        "corrected_text": "I mean, if you really want to go",
        "edit_ratio": 0.31
      },
      "suggested_action": "rollback_to_original_text"
    }
  ],
  "signals": {
    "duration_sec": 3.25,
    "word_count": 12,
    "fallback_used": true
  }
}
```

输出的关键字段建议包括：

- `passed`
- `score`
- `reasons`
- `signals`
- `suggested_action`


## 7. 失败原因分类

第一版建议先固定失败类型枚举，便于统计。

### 7.1 `text_audio_mismatch`

文本与音频内容不一致。

典型情况：

- 修正后的句子明显偏离原音频
- 文本读起来完整，但不对应切片中的实际发音


### 7.2 `boundary_bad`

边界切得不好。

典型情况：

- 句首不完整
- 句尾被截断
- 上一句尾部混入当前片段
- 下一句开头被切进当前片段


### 7.3 `timing_abnormal`

时间戳异常。

典型情况：

- 时长过短或过长
- start/end 重叠
- 词数和时长严重不匹配


### 7.4 `low_asr_confidence`

原始转写质量过低。

典型情况：

- WhisperX 原始词置信度整体偏低
- 音频本身噪音太大


### 7.5 `correction_overreach`

ASR 修正过头。

典型情况：

- 修正前后差异过大
- 修正后的文本不再能稳定映射回原始词时间戳


### 7.6 `mapping_failed`

句子和词时间戳映射失败。

典型情况：

- 找不到句子 anchor
- 只能依赖弱 fallback 才勉强匹配


### 7.7 `subtitle_asr_conflict`

字幕与 ASR 冲突。

这类问题在 YouTube 字幕源不稳定时尤其重要。


### 7.8 `audio_slice_bad`

音频切片本身有问题。

典型情况：

- 切在爆破音中间
- 句首吃字
- 句尾吞音


## 8. 第一版最小实现建议

第一版不要过大，先做最有价值的 5 个检查。

### 8.1 `duration_vs_word_count`

检查：

- 时长是否与单词数严重不匹配

目的：

- 快速筛掉明显异常切片


### 8.2 `boundary_gap_check`

检查：

- 左右边界附近静音是否合理
- 切点是否过于贴近词边界

目的：

- 识别被截断或拼接不自然的片段


### 8.3 `fallback_used`

检查：

- `_find_word_sequence()` 是否未正常命中
- 是否退化为只靠首词匹配
- 是否发生“找不到后追加到前一个 segment”

目的：

- 抓出主流程中不稳定的句子映射结果


### 8.4 `correction_diff_ratio`

检查：

- 修正前后文本 edit distance
- 增删改比例

目的：

- 识别修正过度的 segment


### 8.5 `text_words_vs_timestamp_words`

检查：

- 修正后文本 token 序列
- 原始时间戳 token 序列

目的：

- 判断文本是否仍与时间戳 span 一致


## 9. 复查结果后的动作分流

建议第一版支持 4 种结果：

### 9.1 `pass`

直接入库。


### 9.2 `soft_fail`

允许入库，但标记为低可信：

- 暂不用于核心训练推荐
- 后续可进入人工抽检


### 9.3 `hard_fail`

不入库，直接进入失败队列。


### 9.4 `repairable_fail`

问题可修复，进入自动修复链路。

例如：

- 回退到修正前文本
- 重新调整边界
- 与相邻 segment 合并后重切


## 10. 数据落库建议

建议不要只在运行时打印 review 结果，而要持久化。

可选方案一：

在 `Segment` 上新增字段：

- `review_status`
- `review_score`
- `review_json`

可选方案二：

新增 `segment_reviews` 表：

- `id`
- `segment_id`
- `review_version`
- `passed`
- `score`
- `reasons_json`
- `signals_json`
- `created_at`

建议优先考虑单独表，因为后面复查算法会迭代，独立表更方便比较不同版本效果。


## 11. 与当前代码结构的对应关系

结合当前项目代码，复查层最相关的位置包括：

- `backend/services/pipeline.py`
  负责主流程编排，适合插入 review step

- `backend/services/segmenter.py`
  负责切句、匹配、边界调整、ASR 修正

- `backend/services/transcriber.py`
  提供 WhisperX 词级时间戳，是 review 的底层依据

后续实现时，建议新增一个独立模块，例如：

- `backend/services/reviewer.py`

由它统一负责：

- review 输入整理
- 规则检查
- 原因归类
- 结果输出


## 12. 后续扩展方向

当第一版规则复查稳定后，再考虑增加：

- LLM 辅助失败原因解释
- 自动修复策略
- 批量 review 报表
- 失败样本回放页面
- review 结果驱动的主流程参数优化


## 13. 当前建议结论

这个复查 pipeline 值得尽快做。

原因不是它能让系统“显得更聪明”，而是它能让系统：

- 知道哪些结果可信
- 知道哪些结果不可信
- 知道不可信的原因是什么

这是把当前流水线从“能跑”提升到“可控、可迭代、可持续优化”的关键一步。
