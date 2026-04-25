# 边界索引切片备忘录

## 1. 文档目的

本文档记录当前关于“用边界索引替代自由文本切句”的设计共识。

目标是提升切片稳定性：以 WhisperX 的 token 序列作为唯一坐标系，让大模型选择 token 边界，而不是让大模型生成自由句子文本后，再由系统把文本反向匹配到时间戳。


## 2. 核心方向

第一版实现方向采用路线 1：

- 以 WhisperX tokens 作为切片的标准坐标系。
- 让大模型输出 boundary indexes。
- 用确定性规则验证 boundary 输出是否合法。
- 当验证或复查发现问题时，可以进入有限轮的动态修正循环。

大模型在这里应被视为“受约束的决策器”，而不是自由文本生成器。


## 3. Boundary 协议

第一版采用如下 boundary 定义：

```text
boundary = 一个 segment 的最后一个 token index
```

最后一个 boundary 必须等于最后一个 WhisperX token 的 index。

例如：

```text
0 I
1 want
2 to
3 talk
4 about
5 this
6 today
```

如果大模型输出：

```json
{
  "boundaries": [2, 6]
}
```

则生成的 segments 为：

```text
segment 1: token 0..2
segment 2: token 3..6
```

这个约定可以让覆盖验证非常简单，并避免切点语义上的歧义。


## 4. Boundary Validator

boundary validator 只负责判断大模型输出的索引结构是否合法。

第一版采用两阶段切点约束：

```text
Stage A: 系统先生成候选 boundary indexes
Stage B: 大模型只能从候选集合中选择 boundaries
```

候选 boundary 生成规则：

- 如果 `token[i].end` 与 `token[i + 1].start` 的间隔不少于 `120ms`，则 `i` 是候选 boundary。
- 最后一个 token index 永远是候选 boundary，因为它代表最后一个 segment 的结束。

候选集合在提示词中应使用结构化 JSON，而不是简写文本。

示例：

```json
[
  {
    "boundary_index": 2,
    "type": "cut_candidate",
    "meaning": "cut after token 2; the next segment starts at token 3",
    "left_token": "to",
    "right_token": "talk",
    "gap_after_ms": 220,
    "required": false
  },
  {
    "boundary_index": 6,
    "type": "final_required",
    "meaning": "end the final segment at token 6",
    "left_token": "today",
    "right_token": null,
    "gap_after_ms": null,
    "required": true
  }
]
```

这样做的目的是让大模型清楚知道：

- 可以输出的数字是哪一个字段
- 选择该数字的含义是什么
- 普通切点和最后结束点有什么区别
- 最后结束点必须包含

提示词中应强调：

- `candidate_boundaries` 已经由系统根据 timing gaps 生成。
- 大模型不能自行创造或输出不在 `candidate_boundaries` 里的 boundary index。
- 大模型不需要重新判断 `120ms` 规则，只需要在候选集合中做语义选择。

硬性验证规则：

- `boundaries` 不能为空。
- boundaries 必须严格递增。
- 每个 boundary 必须位于 `[0, token_count - 1]` 范围内。
- 最后一个 boundary 必须等于 `token_count - 1`。
- 每个非最终 boundary 都必须来自候选 boundary 集合。

只要满足这些条件，就可以天然保证：

- 没有 token 被遗漏
- 没有 token 被重复覆盖
- segments 之间不会重叠
- 所有 token 都被完整覆盖一次

validator 不负责判断深层语义质量。

音频真实切割时间仍然使用相邻 token 之间的中点：

```text
cut_time = (token[i].end + token[i + 1].start) / 2
```


## 5. 时间戳检查应放在 WhisperX 之后

时间戳合法性检查应该在 WhisperX 完成后立刻执行，而不是作为 LLM boundary validator 的主要职责。

这是单独的输入质量检查层。

WhisperX 时间戳检查建议包括：

- 每个 token 都有 `word`、`start`、`end`
- 每个 token 都满足 `start < end`
- token 时间整体大体递增
- 缺失时间戳的 token 比例可接受
- 能识别异常过长或异常过短的 token
- 能识别严重时间重叠

如果这一层失败，问题不是大模型 boundary 选错了，而是底层转写或对齐结果不可信，后续切片不应继续正常放行。


## 6. 三层验证模型

切片系统建议拆成三层验证。

### 6.1 Transcription Validation

位置：WhisperX 之后。

目的：

- 判断 token 和 timing 材料是否可用
- 在切片前拒绝或标记坏的对齐结果


### 6.2 Boundary Validation

位置：大模型输出 boundaries 之后。

目的：

- 判断 boundary indexes 是否合法
- 在构造 segment 前拒绝非法的大模型输出


### 6.3 Segment Quality Review

位置：segments 构造完成之后。

目的：

- 判断生成的 segments 是否是好的训练单位
- 标记质量风险
- 必要时要求大模型修正 boundaries


## 7. Hard Fail 与 Soft Risk

不是所有检查项都应该作为硬性失败条件。

Hard Fail 主要对应结构错误或底层数据错误。

示例：

- boundary 越界
- boundary 重复
- boundary 非递增
- 缺少最后一个 boundary
- WhisperX timing 不可用

Soft Risk 通常应该作为风险评分信号，而不是直接一票否决。

示例：

- segment 偏短或偏长
- boundary 位于很小的 gap 附近
- boundary 附近几乎没有 pause
- 词数与时长看起来异常

真实口语中，有些合理的语义边界本来就没有明显停顿。这类情况应该被标记为风险，而不是自动判定为错误。


## 8. 动态修正循环

设计允许引入有限轮的动态修正循环。

但循环不能只依赖“大模型认为自己的结果可以接受”。

推荐流程：

```text
大模型提出 boundaries
-> 确定性 boundary validation
-> segment quality review
-> 如果发现问题，把结构化问题报告反馈给大模型
-> 大模型修正 boundaries
-> 最多重复固定轮数
```

大模型可以参与生成和复查，但结构合法性的硬门槛必须由确定性规则负责。

第一版建议最大轮数：

```text
2 到 4 轮
```

一个实用默认值是 3 轮。


## 9. 修正反馈格式

要求大模型修正时，不应给模糊反馈，而应给结构化问题报告。

示例：

```json
{
  "previous_boundaries": [12, 27, 41],
  "problems": [
    {
      "type": "tiny_gap_boundary",
      "after_token": 27,
      "gap_ms": 38,
      "message": "Boundary chosen on a very small gap."
    },
    {
      "type": "overshort_segment",
      "segment_index": 2,
      "duration_sec": 0.92,
      "message": "Segment is too short for a stable training unit."
    }
  ],
  "instruction": "Revise boundaries while preserving semantic completeness and structural validity."
}
```

后续可以把 `message` 改成中文，但结构本身建议保持稳定，方便程序解析。


## 10. ASR Correction 的角色

ASR correction 不应该定义切片边界。

切片边界的事实来源应该是原始 WhisperX token 序列与时间戳。

建议拆成三层：

- `raw token/timestamp layer`
  用于 boundary 决策

- `display text layer`
  可在切片后进行文本修正，用于前端展示

- `index text layer`
  可用于后续规范化索引、词块挖掘和推荐

ASR correction 可以改善展示文本，但不应覆盖或污染用于切片的 token 坐标系。


## 11. 当前共识

当前形成的设计共识如下：

1. 从自由文本 LLM 切句，转向 boundary-index segmentation。
2. 每个 boundary 表示一个 segment 的最后一个 token index。
3. 最后一个 boundary 必须等于最后一个 token index。
4. 用确定性规则验证 boundary 的结构合法性。
5. 时间戳 sanity check 应放在 WhisperX 后，而不是放在 boundary validator 里。
6. 语义质量与训练价值属于 segment review，不属于基础 boundary legality。
7. 支持有限轮动态修正循环，并向大模型提供结构化失败原因。
8. 不能让大模型成为自己输出结果的唯一裁判。
