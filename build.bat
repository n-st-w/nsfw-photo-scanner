@echo off
rem Build dist\NudityScanner.exe (Windows). Requires Python 3.10+ on PATH.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || goto :error
)
.venv\Scripts\python -m pip install --upgrade pip || goto :error
.venv\Scripts\python -m pip install -r requirements-build.txt || goto :error
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name NudityScanner ^
    --collect-all nudenet --collect-binaries onnxruntime --collect-all pillow_heif ^
    nudity_scanner_gui.py || goto :error
.venv\Scripts\python -c "import subprocess,pathlib,time; p=pathlib.Path('selftest.txt'); subprocess.run(['dist/NudityScanner.exe','--selftest',str(p)]); time.sleep(1); r=p.read_text(); p.unlink(); print('Self-test:', r); assert r.startswith('ok')" || goto :error
echo.
echo Built dist\NudityScanner.exe
exit /b 0
:error
echo Build failed.
exit /b 1
