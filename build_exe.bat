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

REM The installer, when Inno Setup 6 is installed (winget install JRSoftware.InnoSetup).
set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo.
    echo No installer made: Inno Setup 6 is not installed. Get it with:  winget install JRSoftware.InnoSetup
    pause
    exit /b 0
)
for /f "delims=" %%d in ('python tools\installer_defines.py') do set "DEFINES=%%d"
REM The family's installer contract (relaunch, update-channel, stopping Accipio first) ships with redactor_common.
set "CONTRACT="
for /f "delims=" %%p in ('python -m redactor_common.release contract-path') do set "CONTRACT=%%p"
if not defined CONTRACT (
    echo.
    echo ERROR: redactor_common's installer contract was not found ^(python -m redactor_common.release contract-path^).
    pause
    exit /b 1
)
echo.
echo Building the installer...
"%ISCC%" /Q %DEFINES% "/DUpdateContract=%CONTRACT%" installer\accipio.iss
if errorlevel 1 (
    echo.
    echo ERROR: the installer build failed. See above.
    pause
    exit /b 1
)
echo.
echo Done: dist\Accipio-*-windows-x64-setup.exe. Release with: python -m redactor_common.release publish
pause
