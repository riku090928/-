"""解析の流れ：音声抽出 → そのまま認識 → 分離して認識 → 速度補正して認識 → まとめ。"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import audio
from .audd import AuddClient, AuddFatalError, BudgetExceeded, Song
from .config import Settings
from .separate import separate

log = logging.getLogger("remix_finder")

SOURCE_LABELS = {"mix": "元の音声", "vocals": "ボーカル", "beat": "ビート"}
METHOD_LABELS = {"direct": "そのまま", "separated": "分離後", "speed": "速度補正後"}


@dataclass
class Hit:
    source: str     # mix / vocals / beat
    method: str     # direct / separated / speed
    start: float    # 動画内の位置（秒）
    speed: float    # 推定した動画の速度（1.0 = 補正なし）
    song: Song


def fmt_time(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


class Analyzer:
    def __init__(self, settings: Settings, client: AuddClient, video_hash: str, work_dir: Path,
                 progress: Callable[[str], None] = lambda m: None):
        self.s = settings
        self.client = client
        self.video_hash = video_hash
        self.work = work_dir
        self.progress = progress
        self.hits: list[Hit] = []
        self.notes: list[str] = []
        self.stopped = False   # 上限到達や致命的エラーでリクエストを止めたか
        self._clip_n = 0

    # ---- 1クリップの認識 ----
    def _recognize(self, src_path: Path, source: str, start: float, speed: float, index: int) -> Optional[Song]:
        if self.stopped:
            return None
        self._clip_n += 1
        wav = self.work / f"clip_{self._clip_n}.wav"
        mp3 = wav.with_suffix(".mp3")
        try:
            audio.make_clip(src_path, wav, start, self.s.clip_seconds, speed)
            if audio.loudness_db(wav) < self.s.silence_threshold_db:
                log.info("無音のためスキップ: %s %s ×%.2f", SOURCE_LABELS[source], fmt_time(start), speed)
                return None
            audio.to_mp3(wav, mp3)
            key = f"{self.video_hash}|{source}|{start:.2f}|{self.s.clip_seconds:g}|{speed:.3f}"
            song = self.client.recognize(mp3, key, {"source": source, "speed": speed, "index": index})
        except BudgetExceeded:
            self.stopped = True
            self.notes.append(f"リクエスト上限（{self.client.budget}回）に達したため、途中で認識を止めました。")
            return None
        except AuddFatalError as e:
            self.stopped = True
            self.notes.append(f"{e}（APIキーや残り回数を確認してください）")
            return None
        except Exception as e:  # 1クリップの失敗で全体を止めない
            log.warning("認識に失敗: %s", e)
            self.notes.append(f"{SOURCE_LABELS[source]} {fmt_time(start)} の認識でエラー: {e}")
            return None
        finally:
            wav.unlink(missing_ok=True)
            mp3.unlink(missing_ok=True)

        label = f"{SOURCE_LABELS[source]} {fmt_time(start)}" + (f" ×{speed:g}補正" if speed != 1.0 else "")
        if song:
            log.info("検出: %s → %s / %s", label, song.artist, song.title)
        else:
            log.info("該当なし: %s", label)
        return song

    def _scan(self, src_path: Path, source: str, method: str, starts: list[float], speed: float = 1.0) -> int:
        found = 0
        for i, t in enumerate(starts):
            song = self._recognize(src_path, source, t, speed, i)
            if song:
                self.hits.append(Hit(source, method, t, speed, song))
                found += 1
        return found

    # ---- 全体の流れ ----
    def run(self, video: Path) -> dict:
        mix = self.work / "audio.wav"
        self.progress("動画から音声を取り出しています…")
        audio.extract_audio(video, mix)
        video.unlink(missing_ok=True)   # 動画は音声を取り出したら不要
        duration = audio.wav_duration(mix)

        all_starts = audio.clip_starts(duration, self.s.clip_seconds, self.s.interval_seconds)
        # 3種類（そのまま・ボーカル・ビート）に配り、残りを速度補正用に取っておく
        per_source = max(1, self.s.max_requests_per_video // 4)
        starts = audio.thin_out(all_starts, per_source)
        log.info("音声の長さ %.1f 秒 / 認識位置 %s", duration, ", ".join(fmt_time(t) for t in starts))

        # 2. そのまま認識
        self.progress("そのままの音声で曲を探しています…")
        self._scan(mix, "mix", "direct", starts)

        # 3. 分離して認識
        stems: dict[str, Path] = {}
        if not self.stopped:
            self.progress("ボーカルとビートに分離しています（数分かかることがあります）…")
            try:
                vocals, beat = separate(mix, self.work / "separated", self.s.demucs_model)
                stems = {"vocals": vocals, "beat": beat}
            except Exception as e:
                log.warning("分離に失敗: %s", e)
                self.notes.append(f"ボーカルとビートの分離に失敗しました: {e}")
        for source, path in stems.items():
            self.progress(f"分離した{SOURCE_LABELS[source]}で曲を探しています…")
            self._scan(path, source, "separated", starts)

        # 4. 速度補正して認識（まだ見つかっていない側だけ）
        targets = {s: p for s, p in stems.items() if not any(h.source == s for h in self.hits)}
        if not stems and not self.hits:
            targets = {"mix": mix}
        for source, path in targets.items():
            if self.stopped:
                break
            self.progress(f"{SOURCE_LABELS[source]}を速度補正して曲を探しています…")
            self._speed_search(path, source, starts)

        # 5. 後片付け（分離した音声は解析にだけ使い、ここで削除）
        shutil.rmtree(self.work / "separated", ignore_errors=True)
        mix.unlink(missing_ok=True)

        return self._summary(duration)

    def _speed_search(self, path: Path, source: str, starts: list[float]) -> None:
        probes = audio.thin_out(starts, self.s.speed_probe_clips)
        for factor in self.s.speed_factors:
            if self.stopped:
                return
            for i, t in enumerate(probes):
                song = self._recognize(path, source, t, factor, i)
                if song:
                    self.hits.append(Hit(source, "speed", t, factor, song))
                    # 当たった倍率で、残りの位置も確認する
                    rest = [x for x in starts if x != t]
                    self._scan(path, source, "speed", rest, factor)
                    return

    # ---- 結果のまとめ ----
    def _group(self, hits: list[Hit]) -> list[dict]:
        groups: dict[str, dict] = {}
        for h in sorted(hits, key=lambda h: h.start):
            g = groups.setdefault(h.song.key, {**h.song.to_dict(), "found": [], "_n": 0})
            g["_n"] += 1
            g["found"].append({
                "time": fmt_time(h.start),
                "method": METHOD_LABELS[h.method],
                "source": SOURCE_LABELS[h.source],
                "speed": h.speed,
            })
        out = sorted(groups.values(), key=lambda g: -g["_n"])
        for g in out:
            g.pop("_n")
        return out

    def _summary(self, duration: float) -> dict:
        mix_hits = [h for h in self.hits if h.source == "mix"]
        vocal = self._group([h for h in self.hits if h.source == "vocals"])
        vocal_fallback = False
        if not vocal and mix_hits:
            vocal = self._group(mix_hits)
            vocal_fallback = True

        vocal_keys = {f"{g['artist'].strip().lower()}|{g['title'].strip().lower()}" for g in vocal}
        beat = self._group([h for h in self.hits if h.source == "beat"])
        beat_fallback = False
        if not beat:
            others = [h for h in mix_hits if h.song.key not in vocal_keys]
            if others:
                beat = self._group(others)
                beat_fallback = True

        return {
            "duration": fmt_time(duration),
            "vocal": vocal,
            "vocal_fallback": vocal_fallback,
            "beat": beat,
            "beat_fallback": beat_fallback,
            "timeline": [
                {"time": fmt_time(h.start), "source": SOURCE_LABELS[h.source],
                 "method": METHOD_LABELS[h.method], "speed": h.speed,
                 "title": h.song.title, "artist": h.song.artist}
                for h in sorted(self.hits, key=lambda h: (h.start, h.source))
            ],
            "notes": self.notes,
            "requests_used": self.client.requests_used,
            "cache_hits": self.client.cache_hits,
            "budget": self.client.budget,
            "mode": self.client.mode,
            "total_requests": self.client.cache.total_usage(),
        }
