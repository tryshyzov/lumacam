#define AppName "LumaLink Studio"
#define AppVersion "0.1.0"
#define AppExe "..\dist\LumaLinkStudio.exe"

[Setup]
AppId={{DB5182A6-0543-46A4-A018-B63504273211}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} (prototype)
AppPublisher=LumaLink
AppComments=Windows desktop prototype for an Android phone camera
SetupIconFile=..\assets\LumaLinkStudio.ico
DefaultDirName={localappdata}\Programs\LumaLink Studio
DefaultGroupName=LumaLink Studio
DisableProgramGroupPage=yes
Uninstallable=yes
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\LumaLinkStudio.exe
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
PrivilegesRequired=lowest
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
[Icons]
Name: "{group}\LumaLink Studio"; Filename: "{app}\LumaLinkStudio.exe"
Name: "{autodesktop}\LumaLink Studio"; Filename: "{app}\LumaLinkStudio.exe"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\LumaLinkStudio.exe"; Description: "Launch LumaLink Studio"; \
    Flags: postinstall nowait skipifsilent runasoriginaluser

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not WizardSilent then
    begin
      if ActiveLanguage = 'russian' then
        MsgBox('LumaLink установлен без собственного драйвера ядра. Для вывода виртуальной камеры установите OBS Studio (OBS Virtual Camera) или Unity Capture.', mbInformation, MB_OK)
      else
        MsgBox('LumaLink was installed without a custom kernel driver. Install OBS Studio (OBS Virtual Camera) or Unity Capture for virtual camera output.', mbInformation, MB_OK);
    end;
  end;
end;
