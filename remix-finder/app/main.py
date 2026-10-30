"""Webサーバー本体。ブラウザで http://localhost:8765 を開いて使う。"""
from __future__ import annotations

import hashlib
import logging
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .audd import AuddClient, ResultCache
from .config import CACHE_DIR, WORK_DIR, load_settings
from .pipeline import Analyzer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("remix_finder")

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="リミックス楽曲ファインダー")

JOBS: dict[str, dict] = {}
_run_lock = threading.Lock()   # 解析は1本ずつ順番に行う

# 前回の終了時に残った一時ファイルを消す
shutil.rmtree(WORK_DIR, ignore_errors=True)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/info")
def info():
    s = load_settings()
    return {
        "mode": s.audd_mode,
        "has_token": bool(s.audd_api_token),
        "max_requests_per_video": s.max_requests_per_video,
        "total_requests": ResultCache(CACHE_DIR).total_usage(),
    }


@app.post("/api/analyze")
def analyze(file: UploadFile = File(...)):
    settings = load_settings()
    if settings.audd_mode == "real" and not settings.audd_api_token:
        raise HTTPException(400, "AUDD_MODE=real ですが、.env に AUDD_API_TOKEN が設定されていません。")

    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True)
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    video = job_dir / f"input{suffix}"

    sha = hashlib.sha256()
    with open(video, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            sha.update(chunk)
            out.write(chunk)

    JOBS[job_id] = {"status": "queued", "message": "順番待ちです…", "filename": file.filename, "result": None}
    log.info("受け取りました: %s (%s)", file.filename, job_id)
    threading.Thread(target=_run_job, args=(job_id, video, sha.hexdigest(), settings), daemon=True).start()
    return {"job_id": job_id, "filename": file.filename}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "解析が見つかりません。")
    return job


def _run_job(job_id: str, video: Path, video_hash: str, settings) -> None:
    job = JOBS[job_id]
    job_dir = video.parent

    def progress(msg: str) -> None:
        job["message"] = msg
        log.info(msg)

    with _run_lock:
        job["status"] = "running"
        try:
            client = AuddClient(settings.audd_mode, settings.audd_api_token,
                                settings.max_requests_per_video, ResultCache(CACHE_DIR))
            result = Analyzer(settings, client, video_hash, job_dir, progress).run(video)
            job["result"] = result
            job["status"] = "done"
            job["message"] = "解析が終わりました。"
            log.info("今回使ったリクエスト数: %d（上限 %d・キャッシュ利用 %d・%sモード）",
                     result["requests_used"], result["budget"], result["cache_hits"],
                     "ダミー" if result["mode"] == "dummy" else "本番")
        except Exception as e:
            log.exception("解析に失敗しました")
            job["status"] = "error"
            job["message"] = f"解析に失敗しました: {e}"
        finally:
            # アップロードされた動画・分離した音声はすべて削除する
            shutil.rmtree(job_dir, ignore_errors=True)
