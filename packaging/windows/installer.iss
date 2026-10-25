#ifndef SourceDir
  #error SourceDir must point to a prepared QFund program bundle.
#endif
#ifndef OutputDir
  #error OutputDir is required.
#endif
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef InstallId
  #define InstallId "QFund.Desktop"
#endif

[Setup]
AppId={#InstallId}
AppName=QFund
AppVersion={#AppVersion}
AppVerName=QFund {#AppVersion}
#ifdef TestRoot
DefaultDirName={#TestRoot}\Program
#else
DefaultDirName={localappdata}\Programs\QFund
#endif
DefaultGroupName=QFund
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SourceDir={#SourceDir}
OutputDir={#OutputDir}
OutputBaseFilename=QFund-Setup-{#AppVersion}
SetupIconFile={#PackagingDir}\assets\qfund.ico
UninstallDisplayName=QFund
UninstallDisplayIcon={app}\QFund.exe
WizardStyle=modern
ShowLanguageDialog=no
Compression=lzma2/fast
SolidCompression=yes
LZMANumBlockThreads=1
CloseApplications=no
RestartApplications=no
AllowCancelDuringInstall=no
SetupMutex={#InstallId}.Setup

[Languages]
Name: "zhcn"; MessagesFile: "compiler:Default.isl,{#PackagingDir}\zh-CN.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"

[Files]
Source: "*"; DestDir: "{app}"; Excludes: "qfund-install.json"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "qfund-install.json"; DestDir: "{app}"; Flags: onlyifdoesntexist

[Icons]
#ifdef TestRoot
Name: "{#TestRoot}\shell\StartMenu\QFund"; Filename: "{app}\QFund.exe"; Parameters: "--data-root ""{code:UserDataRoot}"""; WorkingDir: "{app}"
Name: "{#TestRoot}\shell\StartMenu\卸载 QFund"; Filename: "{uninstallexe}"
Name: "{#TestRoot}\shell\Desktop\QFund"; Filename: "{app}\QFund.exe"; Parameters: "--data-root ""{code:UserDataRoot}"""; WorkingDir: "{app}"; Tasks: desktopicon
#else
Name: "{group}\QFund"; Filename: "{app}\QFund.exe"; Parameters: "--data-root ""{code:UserDataRoot}"""; WorkingDir: "{app}"
Name: "{group}\卸载 QFund"; Filename: "{uninstallexe}"
Name: "{userdesktop}\QFund"; Filename: "{app}\QFund.exe"; Parameters: "--data-root ""{code:UserDataRoot}"""; WorkingDir: "{app}"; Tasks: desktopicon
#endif

[Run]
Filename: "{app}\QFund.exe"; Parameters: "--data-root ""{code:UserDataRoot}"""; Description: "打开 QFund"; Flags: nowait postinstall skipifsilent

[Code]
var
  GuardActive: Boolean;
  ChannelFile, GuardMessage, PreviousApp: String;

function UserDataRoot(Param: String): String;
begin
#ifdef TestRoot
  Result := '{#TestRoot}\UserData';
#else
  Result := ExpandConstant('{localappdata}\QFund');
#endif
end;

function WaitState(Expected: String): Boolean;
var I: Integer; State: String;
begin
  Result := False;
  for I := 0 to 900 do begin
    State := GetIniString('status', 'state', '', ChannelFile);
    if State = Expected then begin Result := True; Exit; end;
    if State = 'failed' then begin
      GuardMessage := GetIniString('status', 'message', '安装维护失败，请保留记录后重试。', ChannelFile);
      Exit;
    end;
    Sleep(50);
  end;
  GuardMessage := '安装维护响应超时，请保留数据目录中的维护记录并核对任务状态。';
end;

function SendCommand(Command, Expected: String): Boolean;
begin
  Result := SaveStringToFile(ChangeFileExt(ChannelFile, '.command'), Command, False);
  if Result then Result := WaitState(Expected);
end;

function CopyUninstallBundle(TempBundle: String): Boolean;
var Items: TArrayOfString; I: Integer; Relative, Source, Destination: String;
begin
  Result := False;
  if not LoadStringsFromFile(ExpandConstant('{app}\bundle-files.txt'), Items) then Exit;
  for I := 0 to GetArrayLength(Items) - 1 do begin
    Relative := Trim(Items[I]);
    if Relative <> '' then begin
      StringChangeEx(Relative, '/', '\', True);
      if (Pos('..', Relative) > 0) or (Pos(':', Relative) > 0) or (Copy(Relative, 1, 1) = '\') then Exit;
      Source := AddBackslash(ExpandConstant('{app}')) + Relative;
      Destination := AddBackslash(TempBundle) + Relative;
      if not ForceDirectories(ExtractFileDir(Destination)) then Exit;
      if not CopyFile(Source, Destination, False) then Exit;
    end;
  end;
  Result := True;
end;

function StartGuard(Mode: String): Boolean;
var TempBundle, Arguments: String; ResultCode: Integer;
begin
  Result := False;
  GuardMessage := '';
  if GuardActive then begin Result := True; Exit; end;
  ChannelFile := ExpandConstant('{tmp}\qfund-maintenance.ini');
  DeleteFile(ChannelFile);
  DeleteFile(ChangeFileExt(ChannelFile, '.command'));
  if IsUninstaller then begin
    TempBundle := ExpandConstant('{tmp}\QFundMaintenance');
    if not CopyUninstallBundle(TempBundle) then begin
      GuardMessage := '程序文件缺失，无法安全准备卸载。请先使用安装包在原位置修复，数据会保留。'; Exit;
    end;
  end else begin
    ExtractTemporaryFiles('{app}\*');
    TempBundle := ExpandConstant('{tmp}\') + '{app}';
  end;
  Arguments := '--project-root "' + UserDataRoot('') + '" --maintenance ' + Mode +
    ' --install-dir "' + ExpandConstant('{app}') + '" --install-id "{#InstallId}" --channel "' + ChannelFile + '"';
  if not Exec(AddBackslash(TempBundle) + 'QFundWorker.exe', Arguments, TempBundle,
    SW_HIDE, ewNoWait, ResultCode) then begin
    GuardMessage := '无法启动安装维护检查，请确认安装包完整。'; Exit;
  end;
  GuardActive := True;
  Result := WaitState('ready');
end;

procedure InitializeWizard;
begin
  RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#InstallId}_is1', 'InstallLocation', PreviousApp);
  WizardForm.WelcomeLabel2.Caption := '安装后点击 QFund 图标，即可在浏览器中使用。无需另装 Python。' + #13#10#13#10 +
    '基金数据单独保存在用户目录，升级和卸载均保留数据。自动获取需要本人在工作台主动启用。';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if (PreviousApp <> '') and
    (CompareText(RemoveBackslashUnlessRoot(ExpandFileName(PreviousApp)), RemoveBackslashUnlessRoot(ExpandFileName(WizardDirValue))) <> 0) then begin
    Result := '已有安装只能在原位置升级。若要更换位置，请先卸载旧版本；用户数据会保留。'; Exit;
  end;
  if not StartGuard('install') then Result := GuardMessage;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if not SendCommand('commit', 'done') then RaiseException(GuardMessage);
    GuardActive := False;
  end;
end;

procedure DeinitializeSetup;
begin
  if GuardActive then begin
    if not SendCommand('rollback', 'done') then Log(GuardMessage);
    GuardActive := False;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := StartGuard('uninstall');
  if not Result then begin
    Log(GuardMessage);
    if not UninstallSilent then MsgBox(GuardMessage, mbError, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    if not SendCommand('remove', 'removed') then RaiseException(GuardMessage);
  end;
  if CurUninstallStep = usPostUninstall then begin
    if not SendCommand('commit', 'done') then RaiseException(GuardMessage);
    GuardActive := False;
  end;
end;

procedure DeinitializeUninstall;
begin
  if GuardActive then begin
    if not SendCommand('rollback', 'done') then Log(GuardMessage);
    GuardActive := False;
  end;
end;
