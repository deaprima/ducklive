; Installer Windows untuk DuckLive (Inno Setup 6). Dibangun lewat: python tools/build.py <versi>
; - Tanpa hak Administrator (dipasang per user di %LOCALAPPDATA%\Programs)
; - Shortcut Desktop + Start Menu, langsung dijalankan setelah selesai
; - Upgrade: aplikasi lama ditutup otomatis; data user (timer, goal, token) tidak tersentuh

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "DuckLive"
#define AppExe "DuckLive.exe"

[Setup]
AppId={{6B0E5C1D-3F7A-4E8B-9C2D-1A4F7E9B3C55}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=DuckLive-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
ShowLanguageDialog=no

[Languages]
#ifexist SourcePath + "Indonesian.isl"
Name: "id"; MessagesFile: "Indonesian.isl"
#else
Name: "en"; MessagesFile: "compiler:Default.isl"
#endif

[CustomMessages]
DesktopIcon=Buat ikon di Desktop
RunNow=Jalankan DuckLive sekarang
AskDeleteData=Hapus juga data DuckLive (timer, goal, riwayat event, token, login Google)?%n%nPilih "No" jika ingin memakai lagi data ini nanti.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"

[Files]
Source: "..\dist\DuckLive\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:RunNow}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "StopApp"

[Code]
procedure StopRunningApp();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp();  { upgrade: tutup versi lama yang masih berjalan }
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox(CustomMessage('AskDeleteData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{userappdata}\DuckLive'), True, True, True);
end;
