"""AudD を呼ばずに動く自動テスト（uv run pytest で実行）。"""
import subprocess
import wave

import numpy as np
import pytest

from app import audio
from app.audd import AuddClient, AuddFatalError, BudgetExceeded, ResultCache, parse_result

SAMPLE_RESULT = {
    "artist": "Artist", "title": "Title", "album": "Album", "song_link": "https://lis.tn/x",
    "apple_music": {"url": "https://music.apple.com/jp/album/x?i=1"},
    "spotify": {"external_urls": {"spotify": "https://open.spotify.com/track/abc"}},
}


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, body):
        self.body = body
        self.calls = 0

    def post(self, url, data, files, timeout):
        assert data["return"] == "apple_music,spotify"
        self.calls += 1
        return FakeResponse(self.body)


@pytest.fixture
def mp3(tmp_path):
    p = tmp_path / "c.mp3"
    p.write_bytes(b"x")
    return p


def test_parse_result_links():
    song = parse_result(SAMPLE_RESULT)
    assert song.apple_music.startswith("https://music.apple.com/")
    assert song.spotify == "https://open.spotify.com/track/abc"
    assert parse_result(None) is None


def test_cache_avoids_second_request(tmp_path, mp3):
    session = FakeSession({"status": "success", "result": SAMPLE_RESULT})
    cache = ResultCache(tmp_path / "cache")
    c1 = AuddClient("real", "t", 10, cache, session)
    assert c1.recognize(mp3, "k").title == "Title"
    # 別の解析（新しいクライアント・ファイルから読み直したキャッシュ）でも再リクエストしない
    c2 = AuddClient("real", "t", 10, ResultCache(tmp_path / "cache"), session)
    assert c2.recognize(mp3, "k").title == "Title"
    assert session.calls == 1 and c2.requests_used == 0 and c2.cache_hits == 1
    assert cache.total_usage() == 1


def test_no_match_is_cached_too(tmp_path, mp3):
    session = FakeSession({"status": "success", "result": None})
    cache = ResultCache(tmp_path)
    AuddClient("real", "t", 10, cache, session).recognize(mp3, "k")
    assert AuddClient("real", "t", 10, cache, session).recognize(mp3, "k") is None
    assert session.calls == 1


def test_budget(tmp_path, mp3):
    session = FakeSession({"status": "success", "result": None})
    c = AuddClient("real", "t", 2, ResultCache(tmp_path), session)
    c.recognize(mp3, "a")
    c.recognize(mp3, "b")
    with pytest.raises(BudgetExceeded):
        c.recognize(mp3, "c")
    assert session.calls == 2


def test_fatal_error(tmp_path, mp3):
    session = FakeSession({"status": "error", "error": {"error_code": 901, "error_message": "limit"}})
    with pytest.raises(AuddFatalError):
        AuddClient("real", "t", 5, ResultCache(tmp_path), session).recognize(mp3, "k")


def test_dummy_mode_never_calls_api(tmp_path, mp3):
    session = FakeSession({})
    c = AuddClient("dummy", "", 5, ResultCache(tmp_path), session)
    assert c.recognize(mp3, "k", {"source": "vocals", "speed": 1.0}).title == "ダミー原曲"
    assert session.calls == 0


def test_clip_starts_and_thin_out():
    assert audio.clip_starts(10, 12, 20) == [0.0]
    assert audio.clip_starts(70, 12, 20) == [0.0, 20.0, 40.0, 58.0]
    assert audio.thin_out(list(range(10)), 3) == [0, 4, 9]
    assert audio.thin_out([1, 2], 5) == [1, 2]


def _peak_hz(path):
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float)
    spec = np.abs(np.fft.rfft(x))
    return np.fft.rfftfreq(len(x), 1 / sr)[spec.argmax()], len(x) / sr


def test_speed_correction_restores_pitch_and_tempo(tmp_path):
    """440Hz の音を 1.25倍速（=550Hz）にした音声が、補正で 440Hz・元の長さに戻ること。"""
    sped = tmp_path / "sped.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=550:d=20",
                    "-ar", "44100", "-ac", "2", str(sped)], check=True)
    out = tmp_path / "clip.wav"
    audio.make_clip(sped, out, start=2, length=12, speed=1.25)
    hz, dur = _peak_hz(out)
    assert abs(hz - 440) < 3
    assert abs(dur - 12) < 0.1
    assert audio.loudness_db(out) > -45  # 無音扱いにならない
