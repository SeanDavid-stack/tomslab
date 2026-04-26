; Inno Setup script for Tom's Lab v1.0.0
; Wraps the PyInstaller one-folder output at dist/tomslab/ into a single
; TomsLab-Setup-<version>.exe that installs to Program Files, registers
; an uninstaller, and drops Start Menu + desktop shortcuts.
;
; Build:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\tomslab.iss
;
; Output: pack-out\TomsLab-Setup-1.0.0.exe

#define AppVersion "1.0.0"
#define AppName    "Tom's Lab"
#define AppExe     "tomslab.exe"
#define RepoRoot   "D:\Toms Lab"

[Setup]
AppId={{5E36F4C8-2F4A-4C28-AA48-3D5D1B4A9B20}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SDE-Software
AppPublisherURL=https://sdes.dev
AppSupportURL=https://github.com/SeanDavid-stack/tomslab
AppUpdatesURL=https://github.com/SeanDavid-stack/tomslab/releases

DefaultDirName={autopf}\TomsLab
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile={#RepoRoot}\LICENSE
OutputDir={#RepoRoot}\pack-out
OutputBaseFilename=TomsLab-Setup-{#AppVersion}
SetupIconFile={#RepoRoot}\packaging\icon.ico
UninstallDisplayIcon={app}\{#AppExe}

Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; PyInstaller one-folder output — includes tomslab.exe and the _internal/
; tree with torch/CUDA DLLs. Everything under dist/tomslab/ ships as-is.
Source: "{#RepoRoot}\dist\tomslab\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; User manual (visible next to the exe so the app can point at it).
Source: "{#RepoRoot}\USER_MANUAL.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\User Manual"; Filename: "{app}\USER_MANUAL.md"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
