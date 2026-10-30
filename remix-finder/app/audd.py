"""AudD API の呼び出し（キャッシュ・回数上限・ダミー応答つき）。"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

API_URL = "https://api.audd.io/"
RETURN_PARAM = "apple_music,spotify"
# トークン不正・利用上限など、続けても無駄なエラー
FATAL_ERROR_CODES = {900, 901, 902}


class BudgetExceeded(Exception):
    """1本の動画あたりのリクエスト上限に達した。"""


class AuddFatalError(Exception):
    """APIキーや利用上限の問題で、これ以上リクエストしても意味がない。"""


@dataclass
class Song:
    title: str
    artist: str
    album: str = ""
    apple_music: str = ""
    spotify: str = ""
    song_link: str = ""

    @property
    def key(self) -> str:
        return f"{self.artist.strip().lower()}|{self.title.strip().lower()}"

    def to_dict(self) -> dict:
        return {
            "title": self.title, "artist": self.artist, "album": self.album,
            "apple_music": self.apple_music, "spotify": self.spotify, "song_link": self.song_link,
        }


def parse_result(result: Optional[dict]) -> Optional[Song]:
    """AudD の result を Song に変換する（見つからなければ None）。"""
    if not result:
        return None
    apple = (result.get("apple_music") or {}).get("url", "") or ""
    sp = result.get("spotify") or {}
    spotify = (sp.get("external_urls") or {}).get("spotify", "") or ""
    if not spotify and isinstance(sp.get("uri"), str) and sp["uri"].startswith("spotify:track:"):
        spotify = "https://open.spotify.com/track/" + sp["uri"].rsplit(":", 1)[-1]
    return Song(
        title=result.get("title", "") or "",
        artist=result.get("artist", "") or "",
        album=result.get("album", "") or "",
        apple_music=apple,
        spotify=spotify,
        song_link=result.get("song_link", "") or "",
    )


class ResultCache:
    """同じ音声への結果をファイルに保存し、再解析ではリクエストを使わない。"""

    def __init__(self, cache_dir: Path):
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.path = cache_dir / "audd_cache.json"
        self.usage_path = cache_dir / "usage.json"
        self._lock = threading.Lock()
        self._data = self._load(self.path, {})

    @staticmethod
    def _load(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def get(self, key: str):
        """(あるか, result) を返す。result は None（該当なし）もありうる。"""
        with self._lock:
            if key in self._data:
                return True, self._data[key]
            return False, None

    def put(self, key: str, result: Optional[dict]) -> None:
        with self._lock:
            self._data[key] = result
            self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")

    def add_usage(self, n: int = 1) -> int:
        """これまでの累計リクエスト数を記録して返す。"""
        with self._lock:
            usage = self._load(self.usage_path, {"total_requests": 0})
            usage["total_requests"] = int(usage.get("total_requests", 0)) + n
            self.usage_path.write_text(json.dumps(usage), encoding="utf-8")
            return usage["total_requests"]

    def total_usage(self) -> int:
        with self._lock:
            return int(self._load(self.usage_path, {"total_requests": 0}).get("total_requests", 0))


class AuddClient:
    """1本の動画の解析ごとに作る。リクエスト数を数え、上限を超えたら止める。"""

    def __init__(self, mode: str, token: str, budget: int, cache: ResultCache, session=None):
        self.mode = mode
        self.token = token
        self.budget = budget
        self.cache = cache
        self.session = session or requests.Session()
        self.requests_used = 0   # 今回の解析で実際に AudD に送った数（ダミーは仮想の数）
        self.cache_hits = 0

    @property
    def remaining(self) -> int:
        return self.budget - self.requests_used

    def recognize(self, mp3: Path, cache_key: str, dummy_hint: Optional[dict] = None) -> Optional[Song]:
        if self.mode == "dummy":
            return self._dummy(dummy_hint or {})

        found, result = self.cache.get(cache_key)
        if found:
            self.cache_hits += 1
            return parse_result(result)

        if self.remaining <= 0:
            raise BudgetExceeded()

        self.requests_used += 1
        self.cache.add_usage(1)
        with open(mp3, "rb") as f:
            resp = self.session.post(
                API_URL,
                data={"api_token": self.token, "return": RETURN_PARAM},
                files={"file": (mp3.name, f, "audio/mpeg")},
                timeout=60,
            )
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            err = body.get("error") or {}
            code = err.get("error_code")
            msg = f"AudD エラー {code}: {err.get('error_message', '不明なエラー')}"
            if code in FATAL_ERROR_CODES:
                raise AuddFatalError(msg)
            raise RuntimeError(msg)

        result = body.get("result")
        self.cache.put(cache_key, result)
        return parse_result(result)

    def _dummy(self, hint: dict) -> Optional[Song]:
        """APIを呼ばずに決まった答えを返す（画面や流れの確認用）。

        - そのまま（mix）: 最初のクリップだけ「ダミー曲A」
        - 分離後のボーカル: 常に「ダミー原曲」
        - 分離後のビート: そのままでは見つからず、×1.25 の速度補正で「ダミービート」
        """
        if self.remaining <= 0:
            raise BudgetExceeded()
        self.requests_used += 1
        source, speed, index = hint.get("source"), hint.get("speed", 1.0), hint.get("index", 0)
        if source == "mix" and speed == 1.0 and index == 0:
            return Song("ダミー曲A", "ダミーアーティストA",
                        apple_music="https://music.apple.com/", spotify="https://open.spotify.com/")
        if source == "vocals" and speed == 1.0:
            return Song("ダミー原曲", "ボーカル担当（ダミー）",
                        apple_music="https://music.apple.com/", spotify="https://open.spotify.com/")
        if source == "beat" and abs(speed - 1.25) < 1e-6:
            return Song("ダミービート", "トラックメイカー（ダミー）",
                        apple_music="https://music.apple.com/", spotify="https://open.spotify.com/")
        return None
