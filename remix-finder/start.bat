@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
title リミックス楽曲ファインダー

rem winget や uv で入れたばかりのコマンドもすぐ使えるようにする
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"

echo.
echo === リミックス楽曲ファインダー ===
echo.

rem ---- uv（Python とライブラリを自動で用意してくれる道具）----
where uv > nul 2>&1
if errorlevel 1 (
  echo [準備] uv をインストールします...
  call :need_winget || goto :fail
  winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements
  where uv > nul 2>&1 || goto :restart
)

rem ---- ffmpeg（動画から音声を取り出す道具）----
where ffmpeg > nul 2>&1
if errorlevel 1 (
  echo [準備] ffmpeg をインストールします...
  call :need_winget || goto :fail
  winget install --id=Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
  where ffmpeg > nul 2>&1 || goto :restart
)

rem ---- 設定ファイル .env ----
if not exist ".env" (
  copy ".env.example" ".env" > nul
  echo [準備] .env を作りました（いまはダミーモードです）。
)

echo [準備] Python とライブラリを確認しています（初回は10分ほどかかることがあります）...
uv sync --no-dev
if errorlevel 1 goto :fail

echo.
uv run --no-dev python run.py
if errorlevel 1 goto :fail
goto :eof

:need_winget
where winget > nul 2>&1 && exit /b 0
echo.
echo winget が見つかりません。Microsoft Store で「アプリ インストーラー」を入れてから、もう一度 start.bat を実行してください。
exit /b 1

:restart
echo.
echo インストールが終わりました。この画面を閉じて、もう一度 start.bat をダブルクリックしてください。
pause
exit /b 0

:fail
echo.
echo エラーが起きました。上のメッセージを Claude Code に貼り付けて相談してください。
pause
exit /b 1
