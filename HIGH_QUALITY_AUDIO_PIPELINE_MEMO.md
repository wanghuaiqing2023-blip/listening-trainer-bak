# 高质量音频训练链路备忘录

## 1. 背景

当前 YouTube 导入链路的核心目标是尽快完成转录，因此系统会直接把下载结果转成适合 WhisperX 的工作音频：

- 单声道
- 16kHz
- WAV

这对 ASR 很合理，但对听力训练并不理想。因为后续用户实际听到的训练切片，当前也是从这份降质后的 ASR 工作音频中切出来的。

这意味着：

- 用户训练时听到的不是最佳音质
- 我们丢失了 YouTube 原始较高质量音频的价值
- ASR 需求和用户播放需求被错误地绑定到了同一份音频文件

## 2. 当前问题

当前实现实际上把一份音频同时承担了两种职责：

1. 给 WhisperX 转录
2. 给用户切片播放

这会导致一个结构性问题：

- WhisperX 希望音频是 `16kHz + mono` 的标准工作格式
- 用户听力训练希望音频尽可能保留 YouTube 提供的较高质量

所以当前链路虽然方便，但并不符合产品目标。

## 3. 需求结论

后续必须将音频链路拆成两条：

1. 高质量源音频链路
2. ASR 工作音频链路

原则如下：

- 用户训练切片必须来自高质量源音频
- WhisperX 只使用专门准备的 ASR 工作音频
- 不能再从 `16kHz mono wav` 上直接切用户训练片段

## 4. 目标方案

建议将 YouTube 处理链路改成如下形式：

1. 先下载并缓存高质量源媒体或高质量源音频
2. 从高质量源音频派生出一份 ASR 工作音频
3. WhisperX 只使用 ASR 工作音频转录
4. 训练片段从高质量源音频切出

推荐流程：

```text
YouTube URL
-> 下载高质量源媒体 / 高质量源音频
-> 缓存源文件
-> 从源文件导出 asr.wav（16kHz / mono）
-> WhisperX 转录 asr.wav
-> 根据时间戳从高质量源音频切训练片段
-> 用户播放高质量片段
```

## 5. 建议缓存结构

每个任务目录下建议保留以下文件：

```text
data/artifacts/content_<id>/
  cached-source.*
  cached-source-audio.*
  cached-asr.wav
  transcribe-whisperx_result.json
  transcribe-validation.json
  segments-raw.json
  segments-corrected.json
  segments-explained.json
  segments-detected.json
  vocabulary-result.json
```

说明：

- `cached-source.*`
  - 原始媒体文件或最原始的下载文件
- `cached-source-audio.*`
  - 从源媒体中提取出的高质量音频版本
  - 如果下载得到的本身就是高质量音频，则可直接复用
- `cached-asr.wav`
  - 仅供 WhisperX 使用的工作音频
  - 格式可以继续保持 `16kHz mono wav`

## 6. 恢复与重试原则

后续任务恢复应遵循以下规则：

- 如果高质量源文件已存在，不重复下载
- 如果 `cached-asr.wav` 已存在，不重复导出
- 如果 `transcribe-whisperx_result.json` 已存在，不重复跑 WhisperX
- 如果只是后续步骤失败，应继续复用前面已经成功的缓存结果

这能避免：

- 重复下载 YouTube
- 重复导出 ASR 音频
- 重复进行长时间 WhisperX 转录

## 7. 对现有步骤语义的影响

当前 `extract_audio` 这一步的语义需要重新明确。

建议改成更清楚的两步：

1. `prepare_source_audio`
   - 得到高质量源音频
2. `prepare_asr_audio`
   - 从高质量源音频导出 WhisperX 工作音频

这样：

- 用户训练用音频来源清晰
- ASR 音频来源清晰
- 排查问题时也更容易判断到底是哪条链路出了问题

## 8. 验收标准

实现完成后，至少应满足以下条件：

1. YouTube 任务处理后，任务目录中能看到高质量源音频或源媒体缓存。
2. 任务目录中能看到单独的 `cached-asr.wav`。
3. WhisperX 转录明确使用 `cached-asr.wav`。
4. 用户训练片段明确从高质量源音频切出，而不是从 `cached-asr.wav` 切出。
5. 同一任务重试时，已存在的源音频、ASR 音频、转录结果都可以被复用。

## 9. 结论

这不是单纯的音质优化，而是产品目标与技术链路之间的一次必要对齐。

如果产品目标是“给用户做高质量听力训练”，那么：

- 高质量源音频必须保留
- ASR 工作音频必须独立
- 训练切片必须从高质量源音频生成

否则用户最终听到的始终只是“适合机器转录的音频”，而不是“适合人训练听力的音频”。
