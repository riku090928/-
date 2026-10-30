"""Demucs でボーカルとビート（ボーカル以外）に分ける。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


class SeparationError(RuntimeError):
    pass


def separate(audio_wav: Path, out_dir: Path, model: str = "htdemucs") -> tuple[Path, Path]:
    """(ボーカル, ビート) の wav パスを返す。"""
    out_dir.mkdir(parents=True, exist_ok=True)

    # テスト用：Demucs を動かさずに元の音声をそのままコピーする
    if os.environ.get("REMIX_FINDER_FAKE_SEPARATION") == "1":
        vocals, beat = out_dir / "vocals.wav", out_dir / "no_vocals.wav"
        shutil.copy(audio_wav, vocals)
        shutil.copy(audio_wav, beat)
        return vocals, beat

    cmd = [
        sys.executable, "-m", "demucs",
        "--two-stems=vocals",
        "-n", model,
        "-o", str(out_dir),
        "--filename", "{stem}.{ext}",
        str(audio_wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SeparationError(f"Demucs の実行に失敗しました: {proc.stderr.strip()[-800:]}")

    vocals = out_dir / model / "vocals.wav"
    beat = out_dir / model / "no_vocals.wav"
    if not vocals.exists() or not beat.exists():
        raise SeparationError("Demucs の出力ファイルが見つかりません。")
    return vocals, beat
