"""ffmpeg を使った音声処理（抽出・切り出し・速度補正・音量チェック）。"""
from __future__ import annotations

import math
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100


class AudioError(RuntimeError):
    pass


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise AudioError("ffmpeg が見つかりません。start.bat を実行し直してください。")
    return path


def _run(args: list[str]) -> None:
    proc = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise AudioError(f"ffmpeg の実行に失敗しました: {proc.stderr.strip()[-500:]}")


def extract_audio(video: Path, out_wav: Path) -> None:
    """動画から音声だけを取り出す（44.1kHz ステレオ wav）。"""
    _run(["-i", str(video), "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out_wav)])


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def make_clip(src: Path, out: Path, start: float, length: float, speed: float = 1.0) -> None:
    """src の start 秒から切り出して out に保存する。

    speed は「動画が元の曲の何倍速になっているか」の推定値。
    サンプリングレートを読み替える方法（asetrate）で、速度とピッチを同時に元へ戻す。
    戻した後の長さが length 秒になるよう、元動画からは length / speed 秒を切り出す。
    """
    in_len = length / speed
    args = ["-ss", f"{start:.3f}", "-t", f"{in_len:.3f}", "-i", str(src), "-ac", "1"]
    if abs(speed - 1.0) > 1e-6:
        args += ["-af", f"asetrate={SAMPLE_RATE / speed:.3f},aresample={SAMPLE_RATE}"]
    args += ["-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out)]
    _run(args)


def to_mp3(wav: Path, out: Path) -> None:
    """AudD に送る前に mp3 にして通信量を減らす。"""
    _run(["-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k", str(out)])


def loudness_db(path: Path) -> float:
    """クリップの平均音量（dBFS）。無音のクリップを AudD に送らないために使う。"""
    with wave.open(str(path), "rb") as w:
        frames = w.readframes(w.getnframes())
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float64) / 32768.0
    if data.size == 0:
        return -math.inf
    rms = math.sqrt(float(np.mean(data ** 2)))
    return 20 * math.log10(rms) if rms > 0 else -math.inf


def clip_starts(duration: float, clip_len: float, interval: float) -> list[float]:
    """音声全体を一定間隔で切り出すときの開始位置（秒）。"""
    if duration <= clip_len:
        return [0.0]
    last = duration - clip_len
    starts = []
    t = 0.0
    while t <= last + 1e-6:
        starts.append(round(t, 2))
        t += interval
    if last - starts[-1] > interval / 2:
        starts.append(round(last, 2))
    return starts


def thin_out(items: list, limit: int) -> list:
    """均等な間隔で limit 個まで間引く。"""
    if limit <= 0:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (limit - 1)
    return [items[round(i * step)] for i in range(limit)]
