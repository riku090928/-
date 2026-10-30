"""アプリを起動してブラウザを開く（start.bat から呼ばれる）。"""
import threading
import webbrowser

import uvicorn

from app.config import load_settings

if __name__ == "__main__":
    port = load_settings().port
    url = f"http://localhost:{port}"
    print(f"\n  リミックス楽曲ファインダーを起動しました → {url}\n  終了するときはこの黒い画面を閉じてください。\n")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")
