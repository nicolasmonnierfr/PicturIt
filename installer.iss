; Script Inno Setup — PicturIt
; Construit un installeur (setup.exe) à partir du build PyInstaller mode dossier.
;
; Prérequis :
;   1) Avoir produit dist\PicturIt\ :  pyinstaller build.spec --noconfirm --clean
;   2) Inno Setup 6 installé (https://jrsoftware.org/isdl.php)
;
; Compilation de l'installeur :
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;   -> produit installer_output\PicturIt-Setup-1.0.0.exe

#define MyAppName "PicturIt"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "PicturIt"
#define MyAppExeName "PicturIt.exe"

[Setup]
; AppId unique (NE PAS changer entre versions : permet les mises à jour propres).
AppId={{8F2A1B30-7C4D-4E9A-9F1C-2D3E4F5A6B7C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\PicturIt
DefaultGroupName={#MyAppName}
SetupIconFile=resources\app.ico
DisableProgramGroupPage=yes
; Installation par machine (Program Files) par défaut ; l'utilisateur peut
; choisir une installation par utilisateur (sans droits admin) au lancement.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=installer_output
OutputBaseFilename=PicturIt-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; \
    GroupDescription: "Raccourcis supplémentaires :"; Flags: unchecked

[Files]
; Tout le dossier produit par PyInstaller (exe + _internal + bin + resources…).
Source: "dist\PicturIt\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Désinstaller {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent
