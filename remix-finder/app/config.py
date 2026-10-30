"""設定の読み込み（settings.ini と .env）。"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT / "work"      # アップロード動画・分離音声の一時置き場（処理後に削除）
CACHE_DIR = ROOT / "cache"    # AudD の結果キャッシュと利用回数の記録


@dataclass
class Settings:
    audd_api_token: str = ""
    audd_mode: str = "dummy"  # "dummy"（APIを呼ばない） or "real"
    clip_seconds: float = 12.0
    interval_seconds: float = 20.0
    min_positions: int = 4
    max_requests_per_video: int = 30
    speed_factors: list[float] = field(default_factory=lambda: [1.25, 0.8])
    speed_probe_clips: int = 2
    silence_threshold_db: float = -45.0
    demucs_model: str = "htdemucs"
    port: int = 8765


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env", override=False)

    ini = configparser.ConfigParser()
    ini.read(ROOT / "settings.ini", encoding="utf-8")
    a = ini["analysis"] if ini.has_section("analysis") else {}
    srv = ini["server"] if ini.has_section("server") else {}
    d = Settings()

    token = os.environ.get("AUDD_API_TOKEN", "").strip()
    mode = os.environ.get("AUDD_MODE", "dummy").strip().lower()
    if mode not in ("dummy", "real"):
        mode = "dummy"

    factors_raw = a.get("speed_factors", "")
    factors = [float(x) for x in factors_raw.replace("、", ",").split(",") if x.strip()]

    return Settings(
        audd_api_token=token,
        audd_mode=mode,
        clip_seconds=float(a.get("clip_seconds", d.clip_seconds)),
        interval_seconds=float(a.get("interval_seconds", d.interval_seconds)),
        min_positions=int(a.get("min_positions", d.min_positions)),
        max_requests_per_video=int(a.get("max_requests_per_video", d.max_requests_per_video)),
        speed_factors=factors or d.speed_factors,
        speed_probe_clips=int(a.get("speed_probe_clips", d.speed_probe_clips)),
        silence_threshold_db=float(a.get("silence_threshold_db", d.silence_threshold_db)),
        demucs_model=a.get("demucs_model", d.demucs_model).strip() or d.demucs_model,
        port=int(srv.get("port", d.port)),
    )
