#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
#ifndef SourceExe
  #error SourceExe must name the verified CrossTrans2.exe build
#endif

[Setup]
AppId={{AD8D6A46-A731-4999-88EF-0C1A785A2F11}
AppName=CrossTrans 2
AppVersion={#AppVersion}
AppPublisher=CrossTrans
AppPublisherURL=https://github.com/Masaru-urasaM/CrossTrans2
AppSupportURL=https://github.com/Masaru-urasaM/CrossTrans2/issues
DefaultDirName={autopf}\CrossTrans 2
DefaultGroupName=CrossTrans 2
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=admin
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=CrossTrans2Setup_v{#AppVersion}
SetupIconFile=..\src\assets\CrossTrans.ico
UninstallDisplayIcon={app}\CrossTrans2.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupMutex=Global\CrossTrans2Setup
UsePreviousAppDir=yes
UsePreviousTasks=yes
VersionInfoVersion={#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "CrossTrans2.exe"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\CrossTrans 2"; Filename: "{app}\CrossTrans2.exe"
Name: "{autodesktop}\CrossTrans 2"; Filename: "{app}\CrossTrans2.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\CrossTrans2.exe"; Description: "Launch CrossTrans 2"; Flags: nowait postinstall skipifsilent runasoriginaluser; Check: not IsUpdate
Filename: "{app}\CrossTrans2.exe"; Flags: nowait runasoriginaluser; Check: IsUpdate

[Code]
function OpenProcess(Access: LongWord; Inherit: Boolean; Pid: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') <> '0';
end;

function InitializeSetup: Boolean;
var
  Pid: Integer;
  Process: THandle;
  ReadyFile, ReadyToken: String;
  Attempts: Integer;
  Exited: Boolean;
begin
  Result := True;
  Pid := StrToIntDef(ExpandConstant('{param:WAITPID|0}'), 0);
  if Pid > 0 then begin
    ReadyFile := ExpandConstant('{param:READYFILE|}');
    ReadyToken := ExpandConstant('{param:READYTOKEN|}');
    if (ReadyFile <> '') and (ReadyToken <> '') then begin
      if not SaveStringToFile(ReadyFile, ReadyToken, False) then begin
        MsgBox('Could not notify CrossTrans 2 that the installer is ready.', mbError, MB_OK);
        Result := False;
        Exit;
      end;
    end;
    Process := OpenProcess($00100000, False, Pid);
    if Process <> 0 then begin
      Attempts := 0;
      Exited := False;
      while Attempts < 300 do begin
        if WaitForSingleObject(Process, 100) = 0 then begin
          Exited := True;
          Break;
        end;
        if (ReadyFile <> '') and not FileExists(ReadyFile) then Break;
        Attempts := Attempts + 1;
      end;
      CloseHandle(Process);
      if ReadyFile <> '' then DeleteFile(ReadyFile);
      Result := Exited;
      if not Result and (Attempts >= 300) then
        MsgBox('CrossTrans 2 is still running. Close it and run the installer again.', mbError, MB_OK);
    end;
  end;
end;
