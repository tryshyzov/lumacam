#define AppName "LumaLink Studio"
; CI passes the release version with /DAppVersion=x.y.z.
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppExe "..\dist\LumaLinkStudio.exe"
#define CameraBuild "..\virtualcam\Build"

[Setup]
AppId={{DB5182A6-0543-46A4-A018-B63504273211}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} (prototype)
AppPublisher=LumaLink
AppComments=Windows desktop prototype for an Android phone camera
SetupIconFile=..\assets\LumaLinkStudio.ico
DefaultDirName={autopf}\LumaLink Studio
DefaultGroupName=LumaLink Studio
DisableProgramGroupPage=yes
Uninstallable=yes
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\LumaLinkStudio.exe
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
PrivilegesRequired=admin
; Applications that have used the camera keep its DLL loaded. Never close
; them: a newer DLL is put in place on the next restart instead.
CloseApplications=no
RestartIfNeededByRun=yes
OutputDir=..\dist\installer
OutputBaseFilename=LumaLinkStudio-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#AppExe}"; DestDir: "{app}"; DestName: "LumaLinkStudio.exe"; \
    Flags: ignoreversion
; LumaLink Camera, the virtual camera other applications see. It is replaced
; only by a newer build (comparetimestamp), and on restart if it is in use.
Source: "{#CameraBuild}\Release-LumaLinkCamera64\LumaLinkCamera64.dll"; DestDir: "{app}"; \
    Flags: comparetimestamp regserver 64bit restartreplace uninsrestartdelete
Source: "{#CameraBuild}\Release-LumaLinkCamera32\LumaLinkCamera32.dll"; DestDir: "{app}"; \
    Flags: comparetimestamp regserver 32bit restartreplace uninsrestartdelete
Source: "..\virtualcam\LICENSE.txt"; DestDir: "{app}"; DestName: "LumaLinkCamera-LICENSE.txt"; \
    Flags: ignoreversion
[Icons]
Name: "{group}\LumaLink Studio"; Filename: "{app}\LumaLinkStudio.exe"
Name: "{autodesktop}\LumaLink Studio"; Filename: "{app}\LumaLinkStudio.exe"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\LumaLinkStudio.exe"; Description: "Launch LumaLink Studio"; \
    Flags: postinstall nowait skipifsilent runasoriginaluser
; An update started from the app (camera_studio/updates.py) starts it again.
Filename: "{app}\LumaLinkStudio.exe"; Flags: nowait runasoriginaluser; Check: Relaunching

[Code]
const
  { Held by LumaLink Studio while it runs; see camera_studio/updates.py. }
  AppMutex = 'LumaLinkStudioRunning';
  QuitWaitMs = 15000;

function Relaunching: Boolean;
begin
  Result := Pos('/RELAUNCH', Uppercase(GetCmdTail)) > 0;
end;

{ The app quits right after starting an update; wait for it to go so its
  files can be replaced. An interactive install asks to close it instead. }
function InitializeSetup: Boolean;
var
  Waited: Integer;
begin
  Result := True;
  Waited := 0;
  while CheckForMutexes(AppMutex) do
  begin
    if WizardSilent then
    begin
      if Waited >= QuitWaitMs then
      begin
        Log('LumaLink Studio is still running; not updating.');
        Result := False;
        Exit;
      end;
      Sleep(250);
      Waited := Waited + 250;
    end
    else
    begin
      if ActiveLanguage = 'russian' then
        Result := MsgBox('LumaLink Studio запущена. Закройте её и нажмите «Повтор».', mbError, MB_RETRYCANCEL) = IDRETRY
      else
        Result := MsgBox('LumaLink Studio is running. Close it, then choose Retry.', mbError, MB_RETRYCANCEL) = IDRETRY;
      if not Result then
        Exit;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not WizardSilent then
    begin
      if ActiveLanguage = 'russian' then
        MsgBox('Установлена виртуальная камера LumaLink Camera. Выберите её в списке камер в Zoom, Discord, браузере или OBS.', mbInformation, MB_OK)
      else
        MsgBox('The LumaLink Camera virtual camera was installed. Select it in the camera list of Zoom, Discord, your browser or OBS.', mbInformation, MB_OK);
    end;
  end;
end;
