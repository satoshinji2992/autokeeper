; Compile with ISCC /DAppVersion=0.0.5.6 /DAppSource="..." /DOutputFolder="..." installer.iss
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef AppSource
  #error AppSource is required
#endif
#ifndef OutputFolder
  #error OutputFolder is required
#endif

[Setup]
AppId={{C5457906-03C1-4729-A85A-284C43661F1D}
AppName=AutoKeeper
AppVersion={#AppVersion}
AppPublisher=Sato shinji
AppPublisherURL=https://github.com/satoshinji2992/autokeeper
DefaultDirName={localappdata}\Programs\AutoKeeper
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DefaultGroupName=AutoKeeper
DisableProgramGroupPage=yes
OutputDir={#OutputFolder}
OutputBaseFilename=AutoKeeper-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile={#AppSource}\LICENSE
UninstallDisplayIcon={app}\AutoKeeper.exe
CloseApplications=yes
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Icons]
Name: "{autoprograms}\AutoKeeper"; Filename: "{app}\AutoKeeper.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\AutoKeeper"; Filename: "{app}\AutoKeeper.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AutoKeeper.exe"; Description: "启动 AutoKeeper"; Flags: nowait postinstall skipifsilent
