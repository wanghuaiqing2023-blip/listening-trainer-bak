"""
分步测试音频切割流程
用法：
    python test_segmentation.py <YouTube URL 或本地音频路径> [--from-step N]

--from-step N  跳过前 N-1 步，从第 N 步开始（需要上一步已有缓存输出）
每步结果保存到 test_output/ 目录，方便逐步检查。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 把项目根目录加入 path，确保能 import backend.*
sys.path.insert(0, str(Path(__file__).parent))

OUTPUT_DIR = Path(__file__).parent / "test_output"
OUTPUT_DIR.mkdir(exist_ok=True)

CACHE = {
    "audio":     OUTPUT_DIR / "audio.wav",
    "subs":      OUTPUT_DIR / "subtitles.json",
    "whisperx":  OUTPUT_DIR / "whisperx.json",
    "groups":    OUTPUT_DIR / "groups.json",
    "segments":  OUTPUT_DIR / "segments.json",
    "corrected": OUTPUT_DIR / "corrected.json",
}

# ANSI 颜色（终端彩色输出）
_RED   = "\033[31m"
_GREEN = "\033[32m"
_RESET = "\033[0m"


# ── 打印工具 ──────────────────────────────────────────────────────────────────

def header(step: int, title: str):
    print(f"\n{'='*60}")
    print(f"  Step {step}: {title}")
    print(f"{'='*60}")

def ok(msg: str):   print(f"  [OK]  {msg}")
def warn(msg: str): print(f"  [!!]  {msg}")
def info(msg: str): print(f"  [--]  {msg}")


# ── Step 1: 下载字幕 + 音频 ───────────────────────────────────────────────────

def step1_download(url: str):
    header(1, "下载 YouTube 字幕 + 音频")

    from backend.services.youtube import download_youtube_audio_and_subs, parse_srt

    info(f"URL: {url}")
    info("调用 yt-dlp ...")

    audio_path, subtitle_lines = download_youtube_audio_and_subs(url)

    ok(f"音频: {audio_path}")
    audio_size = Path(audio_path).stat().st_size // 1024
    info(f"文件大小: {audio_size} KB")

    if subtitle_lines:
        ok(f"字幕: {len(subtitle_lines)} 行")
        info("前 5 行字幕:")
        for i, line in enumerate(subtitle_lines[:5]):
            info(f"  [{i}] {line['start']:.2f}s - {line['end']:.2f}s  \"{line['text']}\"")
        CACHE["subs"].write_text(
            json.dumps(subtitle_lines, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ok(f"字幕已保存: {CACHE['subs']}")
    else:
        warn("未找到字幕，后续步骤将只依赖 WhisperX")
        CACHE["subs"].write_text("[]", encoding="utf-8")

    # 复制/记录音频路径
    import shutil
    shutil.copy2(audio_path, CACHE["audio"])
    ok(f"音频已复制到: {CACHE['audio']}")

    return str(CACHE["audio"]), subtitle_lines


# ── Step 2: WhisperX 转录 ─────────────────────────────────────────────────────

def step2_transcribe(audio_path: str):
    header(2, "WhisperX 转录（词级时间戳）")

    from backend.services.transcriber import transcribe

    info(f"音频: {audio_path}")
    info("加载 WhisperX 模型（首次较慢）...")

    result = transcribe(audio_path)

    segments = result.get("segments", [])
    ok(f"共 {len(segments)} 个 WhisperX segment")

    total_words = sum(len(s.get("words", [])) for s in segments)
    ok(f"共 {total_words} 个词")

    # 检查词级时间戳完整性
    words_missing_ts = 0
    for seg in segments:
        for w in seg.get("words", []):
            if "start" not in w or "end" not in w:
                words_missing_ts += 1
    if words_missing_ts:
        warn(f"{words_missing_ts} 个词缺少时间戳（可能影响切割精度）")
    else:
        ok("所有词均有时间戳")

    info("前 3 个 segment 预览:")
    for seg in segments[:3]:
        words_preview = " ".join(w["word"] for w in seg.get("words", [])[:8])
        info(f"  {seg['start']:.2f}s - {seg['end']:.2f}s  \"{seg.get('text','').strip()[:60]}\"")
        info(f"    词: {words_preview} ...")

    CACHE["whisperx"].write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok(f"WhisperX 结果已保存: {CACHE['whisperx']}")

    return result


# ── Step 3: Claude 语义分组 ───────────────────────────────────────────────────

def step3_llm_group(subtitle_lines: list[dict], whisperx_result: dict):
    header(3, "Claude 语义切句")

    from backend.services.openai_service import segment_transcript_text

    # 始终使用 WhisperX 文字，确保 Step 4 词匹配成功
    full_text = " ".join(
        s.get("text", "").strip()
        for s in whisperx_result.get("segments", [])
    ).strip()
    info(f"使用 WhisperX 文字（{len(whisperx_result.get('segments',[]))} 个 segment）")

    info(f"文本总长度: {len(full_text)} 字符")
    info(f"发送给 Claude ...")

    sentences = segment_transcript_text(full_text)
    sentences = [s.strip() for s in sentences if s.strip()]

    ok(f"Claude 返回 {len(sentences)} 个完整句子")

    # 检查句子完整性
    word_counts = [len(s.split()) for s in sentences]
    too_short = [s for s in sentences if len(s.split()) < 3]
    too_long  = [s for s in sentences if len(s.split()) > 30]
    info(f"词数分布: 最短={min(word_counts)}词  最长={max(word_counts)}词  平均={sum(word_counts)/len(word_counts):.1f}词")
    if too_short: warn(f"{len(too_short)} 个句子少于3词（可能不完整）")
    if too_long:  warn(f"{len(too_long)} 个句子超过30词（可能未充分切割）")

    info("前 5 个句子预览:")
    for i, s in enumerate(sentences[:5]):
        info(f"  [{i+1}] ({len(s.split())}词) \"{s}\"")

    CACHE["groups"].write_text(
        json.dumps({"sentences": sentences}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    ok(f"切句结果已保存: {CACHE['groups']}")

    return sentences


# ── Step 4: 时间戳映射 ────────────────────────────────────────────────────────

def step4_map_timestamps(sentences: list[str], whisperx_result: dict):
    header(4, "时间戳映射（句子文字 → WhisperX 词级时间戳）")

    import re

    all_words = [
        w
        for seg in whisperx_result.get("segments", [])
        for w in seg.get("words", [])
        if "start" in w and "end" in w
    ]
    ok(f"WhisperX 词库共 {len(all_words)} 个词")

    def normalize(text):
        return re.sub(r"[^\w\s]", "", text.lower()).split()

    flat_norm = [normalize(w["word"]) for w in all_words]
    flat_norm = [n[0] if n else "" for n in flat_norm]

    def find_anchor(sent_norm, start_from):
        probe = sent_norm[:3]
        for i in range(start_from, len(flat_norm) - len(probe) + 1):
            if flat_norm[i: i + len(probe)] == probe:
                return i
        if sent_norm:
            for i in range(start_from, len(flat_norm)):
                if flat_norm[i] == sent_norm[0]:
                    return i
        return None

    segments_data = []
    unmatched = 0
    word_cursor = 0

    for i, sentence in enumerate(sentences):
        sent_norm = normalize(sentence)
        if not sent_norm:
            continue

        match_start = find_anchor(sent_norm, word_cursor)
        if match_start is None:
            unmatched += 1
            warn(f"  未匹配: \"{sentence[:60]}\"")
            continue

        match_end = min(match_start + len(sent_norm) - 1, len(all_words) - 1)
        seg_words = all_words[match_start: match_end + 1]
        precise_start = all_words[match_start]["start"]
        precise_end   = all_words[match_end]["end"]
        word_cursor   = match_end + 1

        segments_data.append({
            "index": len(segments_data),
            "text": sentence,
            "precise_start": precise_start,
            "precise_end": precise_end,
            "word_count": len(seg_words),
            "duration": round(precise_end - precise_start, 2),
        })

    ok(f"生成 {len(segments_data)} 个片段，{unmatched} 个未匹配")

    durations = [s["duration"] for s in segments_data]
    if durations:
        info(f"时长分布: 最短={min(durations):.1f}s  最长={max(durations):.1f}s  平均={sum(durations)/len(durations):.1f}s")
        too_short = [s for s in segments_data if s["duration"] < 1.0]
        too_long  = [s for s in segments_data if s["duration"] > 20.0]
        if too_short: warn(f"{len(too_short)} 个片段时长 < 1s")
        if too_long:  warn(f"{len(too_long)} 个片段时长 > 20s")

    # 边界调整：把切割点移到相邻片段之间静音间隙的中点
    for i in range(len(segments_data) - 1):
        gap_start = segments_data[i]["precise_end"]
        gap_end   = segments_data[i + 1]["precise_start"]
        if gap_end > gap_start:
            mid = (gap_start + gap_end) / 2
            segments_data[i]["precise_end"]          = round(mid, 3)
            segments_data[i + 1]["precise_start"]    = round(mid, 3)
            segments_data[i]["duration"] = round(
                segments_data[i]["precise_end"] - segments_data[i]["precise_start"], 2
            )

    info("前 5 个片段预览（含边界调整）:")
    for i, s in enumerate(segments_data[:5]):
        gap = ""
        if i < len(segments_data) - 1:
            g = segments_data[i+1]["precise_start"] - s["precise_end"]
            gap = f" | 间隙→下句:{g*1000:.0f}ms"
        info(f"  [{s['index']+1}] {s['precise_start']:.3f}s-{s['precise_end']:.3f}s "
             f"({s['duration']}s, {s['word_count']}词){gap}  \"{s['text'][:60]}\"")


    CACHE["segments"].write_text(
        json.dumps(segments_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok(f"片段数据已保存: {CACHE['segments']}")

    return segments_data


# ── Step 4.5: 文字纠错 ────────────────────────────────────────────────────────

def step4_5_correct(segments_data: list[dict]) -> list[dict]:
    import difflib
    header("4.5", "ASR 文字纠错（颜色标注）")

    from backend.services.openai_service import correct_transcripts

    originals = [s["text"] for s in segments_data]
    info(f"发送 {len(originals)} 个句子给 Claude 纠错 ...")
    corrected = correct_transcripts(originals)

    changed_count = 0
    for i, (orig, corr) in enumerate(zip(originals, corrected)):
        if orig == corr:
            continue
        changed_count += 1
        # 词级 diff，用颜色标注变化
        orig_words = orig.split()
        corr_words = corr.split()
        matcher = difflib.SequenceMatcher(None, orig_words, corr_words)
        orig_colored = []
        corr_colored = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                orig_colored.append(" ".join(orig_words[i1:i2]))
                corr_colored.append(" ".join(corr_words[j1:j2]))
            elif tag == "replace":
                orig_colored.append(f"{_RED}{' '.join(orig_words[i1:i2])}{_RESET}")
                corr_colored.append(f"{_GREEN}{' '.join(corr_words[j1:j2])}{_RESET}")
            elif tag == "delete":
                orig_colored.append(f"{_RED}{' '.join(orig_words[i1:i2])}{_RESET}")
            elif tag == "insert":
                corr_colored.append(f"{_GREEN}{' '.join(corr_words[j1:j2])}{_RESET}")

        print(f"\n  [{i+1}]")
        print(f"  原文: {' '.join(orig_colored)}")
        print(f"  纠正: {' '.join(corr_colored)}")

        # 更新 segments_data
        segments_data[i]["text"] = corr

    if changed_count == 0:
        ok("所有句子无需纠错")
    else:
        ok(f"共纠正 {changed_count}/{len(originals)} 个句子")

    CACHE["corrected"].write_text(
        json.dumps(segments_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok(f"纠错结果已保存: {CACHE['corrected']}")
    return segments_data


# ── Step 5: 音频切片 ──────────────────────────────────────────────────────────

def step5_slice_audio(segments_data: list[dict], audio_path: str):
    header(5, "音频切片（ffmpeg）")

    from backend.utils.audio import slice_audio

    slices_dir = OUTPUT_DIR / "slices"
    slices_dir.mkdir(exist_ok=True)

    ok(f"切片输出目录: {slices_dir}")
    info(f"共 {len(segments_data)} 个片段待切片")

    # 只切前 5 个做验证，避免耗时太长
    preview_count = min(5, len(segments_data))
    warn(f"测试模式：只切前 {preview_count} 个片段（全量切片去掉此限制）")

    results = []
    for seg in segments_data[:preview_count]:
        start = seg["precise_start"]
        end   = seg["precise_end"]
        try:
            out_path = slice_audio(audio_path, start, end, slices_dir)
            size_kb = Path(out_path).stat().st_size // 1024
            ok(f"  [{seg['index']+1}] {start:.2f}s-{end:.2f}s → {Path(out_path).name} ({size_kb}KB)")
            results.append({"index": seg["index"], "path": out_path, "ok": True})
        except Exception as e:
            warn(f"  [{seg['index']+1}] 切片失败: {e}")
            results.append({"index": seg["index"], "error": str(e), "ok": False})

    success = sum(1 for r in results if r["ok"])
    ok(f"成功 {success}/{preview_count} 个")
    info(f"播放验证：用音频播放器打开 {slices_dir} 目录下的文件逐一检查")

    return results


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="分步测试音频切割流程")
    parser.add_argument("source", help="YouTube URL 或本地音频文件路径")
    parser.add_argument(
        "--from-step", type=int, default=1, metavar="N",
        help="从第 N 步开始（使用已缓存的中间结果，默认从第 1 步开始）"
    )
    args = parser.parse_args()

    from_step = args.from_step
    source = args.source

    audio_path      = None
    subtitle_lines  = None
    whisperx_result = None
    groups          = None
    text_lines      = None
    segments_data   = None

    # ── 加载缓存（如果跳过了某些步骤）───────────────────────────────────────
    if from_step > 1:
        if not CACHE["audio"].exists():
            print(f"[错误] 找不到缓存音频 {CACHE['audio']}，请从第 1 步开始")
            sys.exit(1)
        audio_path = str(CACHE["audio"])
        info(f"使用缓存音频: {audio_path}")

    if from_step > 2:
        if not CACHE["subs"].exists() or not CACHE["whisperx"].exists():
            print("[错误] 找不到字幕或 WhisperX 缓存，请从第 1 或 2 步开始")
            sys.exit(1)
        subtitle_lines  = json.loads(CACHE["subs"].read_text(encoding="utf-8"))
        whisperx_result = json.loads(CACHE["whisperx"].read_text(encoding="utf-8"))
        info(f"使用缓存字幕 ({len(subtitle_lines)} 行) 和 WhisperX 结果")

    if from_step > 3:
        if not CACHE["groups"].exists():
            print("[错误] 找不到分组缓存，请从第 3 步开始")
            sys.exit(1)
        cached = json.loads(CACHE["groups"].read_text(encoding="utf-8"))
        sentences = cached["sentences"]
        info(f"使用缓存句子 ({len(sentences)} 句)")

    if from_step > 4:
        if not CACHE["segments"].exists():
            print("[错误] 找不到片段缓存，请从第 4 步开始")
            sys.exit(1)
        segments_data = json.loads(CACHE["segments"].read_text(encoding="utf-8"))
        info(f"使用缓存片段数据 ({len(segments_data)} 个片段)")

    # ── 逐步执行 ────────────────────────────────────────────────────────────
    try:
        if from_step <= 1:
            audio_path, subtitle_lines = step1_download(source)

        if from_step <= 2:
            whisperx_result = step2_transcribe(audio_path)

        if from_step <= 3:
            sentences = step3_llm_group(subtitle_lines, whisperx_result)

        if from_step <= 4:
            segments_data = step4_map_timestamps(sentences, whisperx_result)

        if from_step <= 5:
            segments_data = step4_5_correct(segments_data)
            step5_slice_audio(segments_data, audio_path)

    except KeyboardInterrupt:
        print("\n\n已中断")
        sys.exit(0)
    except Exception as e:
        import traceback
        print(f"\n[错误] 步骤执行失败: {e}")
        traceback.print_exc()
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  全部完成，中间结果保存在: {OUTPUT_DIR}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
