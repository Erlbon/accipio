@echo off
REM Builds dist\Accipio\Accipio.exe (a folder build) with PyInstaller. Run it on Windows with Python 3.10 or newer on the path.
REM
REM It fetches aria2 into bin\ when it is missing (checked against a pinned SHA-256), does not bump the build number
REM (python bump_version.py does) and does not publish anything.

cd /d "%~dp0"
echo Working directory: %cd%
echo.

python --version
if errorlevel 1 (
    echo.
    echo ERROR: "python" was not found on your PATH. Install Python 3.10 or newer from python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

echo Installing what the program and the build need...
python -m pip install . "pyinstaller>=6.6"
if errorlevel 1 (
    echo.
    echo ERROR: pip failed: no internet, or a proxy or firewall in the way. The message above says which.
    pause
    exit /b 1
)

if not exist bin\aria2\aria2c.exe (
    echo.
    echo Fetching aria2...
    python tools\fetch_engines.py
    if errorlevel 1 (
        echo.
        echo ERROR: aria2 could not be fetched. See above.
        pause
        exit /b 1
    )
)

echo.
echo Building dist\Accipio\Accipio.exe ...
REM "python -m PyInstaller", not "pyinstaller": the script folder of a per-user Python is often not on the path.
python -m PyInstaller accipio.spec --noconfirm
if errorlevel 1 (
    echo.
    echo ERROR: the build failed. See above.
    pause
    exit /b 1
)

echo.
echo Done: dist\Accipio\Accipio.exe  (copy the whole Accipio folder; the exe needs the _internal folder beside it)
pause
