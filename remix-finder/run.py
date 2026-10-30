"""アプリを起動してブラウザを開く（start.bat から呼ばれる）。"""
import socket
import sys
import threading
import urllib.request
import webbrowser

import uvicorn

from app.config import ROOT, load_settings


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def already_running(url: str) -> bool:
    """同じアプリがもう起動しているか（/api/info が返事をするか）。"""
    try:
        with urllib.request.urlopen(url + "/api/info", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def print_env_status() -> None:
    """今のモードと .env の状態を表示する（設定が反映されないときの手がかり）。"""
    s = load_settings()
    if s.audd_mode == "real":
        print(f"  モード: 本番（AudD に問い合わせます。1本あたり最大 {s.max_requests_per_video} 回）\n")
        return
    print("  モード: ダミー（AudD は呼びません）")
    if (ROOT / ".env.txt").exists():
        print("  ※ .env.txt というファイルがあります。名前を .env に変えてください。")
    elif not (ROOT / ".env").exists():
        print("  ※ .env が見つかりません。start.bat と同じフォルダに .env を置いてください。")
    elif s.audd_api_token:
        print("  ※ APIキーは入っています。本番にするには .env の AUDD_MODE=real にしてください。")
    else:
        print("  ※ 本番にするには .env に AUDD_API_TOKEN と AUDD_MODE=real を書いてください。")
    print()


if __name__ == "__main__":
    port = load_settings().port
    url = f"http://localhost:{port}"

    if port_in_use(port):
        if already_running(url):
            print(f"\n  リミックス楽曲ファインダーはすでに起動しています → {url}")
            print("  ブラウザで画面を開きます。設定（.env や settings.ini）を変えたときは、")
            print("  もう1つの黒い画面を閉じてから start.bat を起動し直してください。\n")
            webbrowser.open(url)
            input("  Enter キーを押すとこの画面を閉じます…")
            sys.exit(0)
        print(f"\n  {port} 番のポートを別のアプリが使っています。")
        print(f"  settings.ini の port を別の番号（例: 8766）に変えてから、もう一度起動してください。\n")
        sys.exit(1)

    print(f"\n  リミックス楽曲ファインダーを起動しました → {url}\n  終了するときはこの黒い画面を閉じてください。\n")
    print_env_status()
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")
