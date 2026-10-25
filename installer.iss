; Inno Setup script for Maquette  (built by build_installer.bat)
#define MyAppName "Maquette"
#define MyAppVersion "1.0.0"
#define MyAppExe "Maquette.exe"

[Setup]
AppId={{8F3C2A51-6B7D-4E2A-9C1F-5D4B3A2E1F07}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppName}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=Maquette-Setup-{#MyAppVersion}
SetupIconFile=assets\maquette.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "assoc"; Description: "Open .maq boards with {#MyAppName}"; GroupDescription: "File types:"
Name: "openwith"; Description: "Add {#MyAppName} to ""Open with"" for 3D models and images"; GroupDescription: "File types:"

[Files]
Source: "dist\Maquette\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
; .maq board files
Root: HKA; Subkey: "Software\Classes\.maq"; ValueType: string; ValueName: ""; ValueData: "Maquette.Board"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Maquette.Board"; ValueType: string; ValueName: ""; ValueData: "Maquette Board"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Maquette.Board\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExe},0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Maquette.Board\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"" ""%1"""; Tasks: assoc
; "Open with" entry for models and images
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"" ""%1"""; Flags: uninsdeletekey; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\.obj\OpenWithList\{#MyAppExe}"; Flags: uninsdeletekey; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\.stl\OpenWithList\{#MyAppExe}"; Flags: uninsdeletekey; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\.glb\OpenWithList\{#MyAppExe}"; Flags: uninsdeletekey; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\.ply\OpenWithList\{#MyAppExe}"; Flags: uninsdeletekey; Tasks: openwith

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
