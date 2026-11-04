; Inno Setup script for Accipio's Windows installer: dist\Accipio-<tag>-windows-x64-setup.exe
;
; Built by build_exe.bat after the exe (it passes the build number), or by hand:
;   ISCC /DAppVersion=2026.11.4.1 /DBuildTag=2026-11-04-01 /DBuildName="2026-11-04#01" installer\accipio.iss
;
; A per-user install (%LOCALAPPDATA%\Programs\Accipio, no admin prompt). Settings, rules and the queue live in
; %APPDATA%\Accipio, not here, so updating replaces the program folder and keeps them. The browser link (native
; messaging host, HKCU) is written by "Accipio.exe --register", the same code the app runs at every start.

#ifndef AppVersion
  #error Pass /DAppVersion, /DBuildTag and /DBuildName (build_exe.bat does)
#endif

#define AppName "Accipio"
#define HostKey "no.erlbon.accipio"

[Setup]
AppId={{6C0E5D8A-3F7B-4B8E-9A51-2D4C7E1A9B30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#BuildName}
AppPublisher=Erlbon
AppPublisherURL=https://github.com/Erlbon/accipio
AppSupportURL=https://github.com/Erlbon/accipio/issues
AppUpdatesURL=https://github.com/Erlbon/accipio/releases
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SourceDir=..
OutputDir=dist
OutputBaseFilename=Accipio-{#BuildTag}-windows-x64-setup
SetupIconFile=accipio\gui\appicon\accipio.ico
UninstallDisplayIcon={app}\Accipio.exe
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; A running Accipio (and the aria2c it started) holds files in the program folder: close it first.
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Put an Accipio icon on the desktop"; Flags: unchecked
Name: "startup"; Description: "Start Accipio when I sign in to Windows (minimized)"; Flags: unchecked
Name: "ffmpeg"; Description: "Install ffmpeg with winget, for full-quality video from YouTube and other sites (about 100 MB; accepts winget's package agreements)"; Check: OfferFfmpeg

[InstallDelete]
; Leftovers from an older build would otherwise be loaded next to the new files.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\Accipio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\Accipio.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Accipio.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\Accipio.exe"" --minimized"; Tasks: startup; Flags: uninsdeletevalue
; Written by Accipio itself; listed here only so the uninstaller removes them.
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\{#HostKey}"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\{#HostKey}"; Flags: dontcreatekey uninsdeletekey

[Run]
Filename: "{app}\Accipio.exe"; Parameters: "--register"; StatusMsg: "Connecting Chrome and Edge to Accipio..."; Flags: runhidden waituntilterminated
Filename: "{cmd}"; Parameters: "/c winget install --id Gyan.FFmpeg --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity"; StatusMsg: "Installing ffmpeg with winget (this can take a minute)..."; Tasks: ffmpeg; Flags: runhidden waituntilterminated
; Chrome will not open its own extensions page for another program, so this opens a page with the steps and copy buttons.
Filename: "{app}\_internal\accipio\gui\help\connect-browser.html"; Description: "Show how to add the browser extension (one minute, once)"; Check: OfferExtension; Flags: postinstall shellexec nowait
Filename: "{app}\Accipio.exe"; Description: "Start Accipio"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Without /F: Accipio closes as if its window were closed, and stops aria2 on the way out.
Filename: "{sys}\taskkill.exe"; Parameters: "/IM Accipio.exe"; RunOnceId: "CloseAccipio"; Flags: runhidden waituntilterminated
; aria2 takes a few seconds to exit on Windows; until it has, its exe cannot be deleted. Only this install's processes.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -Command ""Get-Process Accipio,aria2c -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{app}\*' } | Wait-Process -Timeout 20 -ErrorAction SilentlyContinue"""; RunOnceId: "WaitForAccipio"; Flags: runhidden waituntilterminated

[UninstallDelete]
Type: files; Name: "{userappdata}\Accipio\native-host.cmd"
Type: files; Name: "{userappdata}\Accipio\{#HostKey}.json"

[Messages]
FinishedLabel=Accipio is installed.%n%nOne step is left, once: adding the browser extension, so the downloads you click in Chrome or Edge come to Accipio. The help page below shows how; Accipio shows the same steps until it is done.

[Code]
var
  IsUpdate: Boolean;
  OtherManagers: String;

function FfmpegPresent: Boolean;
begin
  Result := (FileSearch('ffmpeg.exe', GetEnv('PATH')) <> '')
    or FileExists(ExpandConstant('{localappdata}\Microsoft\WinGet\Links\ffmpeg.exe'))
    or FileExists(ExpandConstant('{commonpf64}\ffmpeg\bin\ffmpeg.exe'))
    or FileExists(ExpandConstant('{localappdata}\Programs\ffmpeg\bin\ffmpeg.exe'));
end;

function OfferFfmpeg: Boolean;
begin
  { winget is an app alias in WindowsApps; FileSearch does not see aliases, so look for the file itself. }
  Result := (not FfmpegPresent) and FileExists(ExpandConstant('{localappdata}\Microsoft\WindowsApps\winget.exe'));
end;

{ Other download managers whose browser extensions also take clicked downloads. Accipio never removes
  them; setup only says so, once, on the last page. }
procedure FindManagersIn(RootKey: Integer);
var
  Names: TArrayOfString;
  I, J: Integer;
  Shown: String;
  Known: array of String;
begin
  Known := ['Free Download Manager', 'Internet Download Manager', 'EagleGet', 'Xtreme Download Manager', 'Motrix',
            'Neat Download Manager', 'Download Accelerator Plus', 'Gopeed', 'JDownloader'];
  if not RegGetSubkeyNames(RootKey, 'Software\Microsoft\Windows\CurrentVersion\Uninstall', Names) then
    exit;
  for I := 0 to GetArrayLength(Names) - 1 do
    if RegQueryStringValue(RootKey, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + Names[I], 'DisplayName', Shown) then
      for J := 0 to GetArrayLength(Known) - 1 do
        if (Pos(Lowercase(Known[J]), Lowercase(Shown)) > 0) and (Pos(Known[J], OtherManagers) = 0) then
        begin
          if OtherManagers <> '' then
            OtherManagers := OtherManagers + ', ';
          OtherManagers := OtherManagers + Known[J];
        end;
end;

function InitializeSetup: Boolean;
begin
  IsUpdate := RegKeyExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6C0E5D8A-3F7B-4B8E-9A51-2D4C7E1A9B30}_is1');
  OtherManagers := '';
  FindManagersIn(HKCU);
  FindManagersIn(HKLM64);
  FindManagersIn(HKLM32);
  Result := True;
end;

function OfferExtension: Boolean;
begin
  { Only on a first install: after an update the extension is already loaded from the same folder. }
  Result := not WizardSilent and not IsUpdate;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID <> wpFinished then
    exit;
  if IsUpdate then
    WizardForm.FinishedLabel.Caption := 'Accipio is updated. Your rules, settings and downloads are kept.';
  if OtherManagers <> '' then
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 +
      OtherManagers + ' is also installed here. If its browser extension is on, switch it off (or Accipio''s) ' +
      'in the extensions page: both would try to take the same downloads. Nothing of it was changed.';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox('Also delete your rules, settings and download list?' + #13#10#13#10 +
              'Choose No to keep them for a later install (they are in ' + ExpandConstant('{userappdata}\Accipio') + ').',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{userappdata}\Accipio'), True, True, True);
end;
