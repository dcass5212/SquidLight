; Inno Setup 6 script for SquidLight. Build with packaging/build.py, which passes
; AppVersion and SourceDir (the PyInstaller folder with openrgb\ and LICENSES\ in it).
;
; Installs the app and its bundled OpenRGB, registers the elevated "run OpenRGB at sign-in"
; scheduled task (SquidLight.exe --register-openrgb-task, run here with admin rights), and
; optionally starts SquidLight with Windows. The uninstaller stops OpenRGB, removes the
; task and the autostart entry (SquidLight.exe --uninstall-cleanup). User data in
; %APPDATA%\RGBApp is kept.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\SquidLight"
#endif

[Setup]
AppId={{EA3CA0F9-6D1C-479D-B55A-61CD1D052B16}
AppName=SquidLight
AppVersion={#AppVersion}
AppVerName=SquidLight {#AppVersion}
AppPublisher=SquidLight
DefaultDirName={autopf}\SquidLight
DefaultGroupName=SquidLight
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=SquidLight-Setup-{#AppVersion}
SetupIconFile=squidlight.ico
UninstallDisplayIcon={app}\SquidLight.exe
UninstallDisplayName=SquidLight
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; the running app holds this mutex (rgbapp/ui/single_instance.py): ask to close it first
AppMutex=SquidLightAppMutex
CloseApplications=yes
RestartApplications=no
; HKCU Run value for "start with Windows" belongs to the user who runs setup
UsedUserAreasWarning=no

[Tasks]
Name: "autostart"; Description: "Start SquidLight when I sign in (in the tray)"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\SquidLight"; Filename: "{app}\SquidLight.exe"
Name: "{autodesktop}\SquidLight"; Filename: "{app}\SquidLight.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SquidLight"; ValueData: """{app}\SquidLight.exe"" --minimized"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\SquidLight.exe"; Parameters: "--register-openrgb-task"; StatusMsg: "Setting up OpenRGB to run in the background..."; Flags: runhidden waituntilterminated
Filename: "{app}\SquidLight.exe"; Description: "Start SquidLight now"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{app}\SquidLight.exe"; Parameters: "--uninstall-cleanup"; Flags: runhidden waituntilterminated; RunOnceId: "SquidLightCleanup"

[Code]
// The task also starts OpenRGB at sign-in, so it may be running without the app.
// Stop it before replacing its files.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN "\SquidLight\OpenRGB"', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Sleep(1000);
  Result := '';
end;
