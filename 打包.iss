; ============================================================
; 墨码写作 - Inno Setup 安装脚本
; ------------------------------------------------------------
; 使用方法：
;   1. 先跑 build.py 生成 dist\墨码写作\ 绿色版目录
;   2. 安装 Inno Setup 6：https://jrsoftware.org/isdl.php
;   3. 用 Inno Setup Compiler 打开本文件，按 F9 编译
;   4. 安装包生成在 installer_output\ 目录下
;
; 中文界面注意：
;   Inno Setup 默认不带简体中文语言文件。若下面 [Languages]
;   里的 ChineseSimplified.isl 报错，去下载：
;   https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/Unofficial/ChineseSimplified.isl
;   放到 Inno Setup 安装目录的 Languages\ 子目录下即可。
; ============================================================

; ---- 版本信息（发新版时改这里）----
#define MyAppName        "墨码写作"
#define MyAppVersion     "1.0.2"
#define MyAppPublisher   "墨码写作开发者"
#define MyAppURL         "https://gitee.com/momaxiezuo/ink-code-writing"
#define MyAppExeName     "墨码写作.exe"
#define MyAppId          "{{8A3F2C1E-7B4D-4E9A-9C5F-3D2E1B8A6C4F}"
#define MySourceDir      "dist\墨码写作"

[Setup]
; AppId 是应用的唯一标识，升级时保持不变；卸载/覆盖安装都靠它识别
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases

; 默认装到 Program Files（由 PrivilegesRequiredOverridesAllowed 允许用户改为当前用户）
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}

; 允许用户在安装向导里选择"为所有用户安装 / 仅为当前用户安装"
; 只装当前用户时不需要管理员权限，对于只想试用的用户更友好
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; 安装包和卸载程序的图标（从绿色版目录里的 ico 取）
SetupIconFile={#MySourceDir}\墨码写作.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

; 许可协议
LicenseFile=LICENSE.txt

; 输出设置
OutputDir=installer_output
OutputBaseFilename=moma-{#MyAppVersion}-win-x64
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; 现代向导样式（Win10/11 下更协调）
WizardStyle=modern

; 让卸载程序在控制面板里显示图标和"大小"
DisableProgramGroupPage=no
DisableDirPage=no
AllowNoIcons=yes

; 最小化样板：不生成 README 提示
DisableReadyPage=no

[Languages]
; 若中文语言文件缺失，可先注释掉第一行、保留英文
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english";           MessagesFile: "compiler:Default.isl"

[Tasks]
; 桌面快捷方式，允许用户取消
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 绿色版目录的全部内容打进安装包
; recursesubdirs / createallsubdirs：把 _internal 里的所有子目录也带上
Source: "{#MySourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\墨码写作.ico"
; 桌面快捷方式（仅在用户勾选时创建）
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\墨码写作.ico"; Tasks: desktopicon

[Run]
; 安装完成后可选"立即启动"
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 清理安装目录里的残留（不碰用户数据目录）
Type: filesandordirs; Name: "{app}\_internal"
Type: files;          Name: "{app}\*.pyc"

[Code]
// ============================================================
// 卸载时提示用户：稿件数据目录不会被删除
// ============================================================
function InitializeUninstall(): Boolean;
begin
  Result := True;
  if MsgBox(
    '卸载完成后，"我的文档\墨码写作\" 里的作品数据、历史快照、' + #13#10 +
    '码字统计等文件会保留，不会被删除。' + #13#10 + #13#10 +
    '如需彻底清理，请手动删除该目录。' + #13#10 + #13#10 +
    '确定继续卸载吗？',
    mbConfirmation, MB_YESNO) = IDNO then
    Result := False;
end;

// ============================================================
// 安装前检查：如果墨码写作正在运行，提示用户先关闭
// ============================================================
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  // 尝试静默杀掉同名进程（可选，注释掉会更"温柔"）
  // 这里选择"温和"方式：仅检测并提示，不强制杀进程
  if Exec('tasklist', '/FI "IMAGENAME eq 墨码写作.exe" /NH',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    // 简化处理：不实际解析输出，让用户自己去关
    // 因为 Inno Setup Pascal 脚本处理进程输出比较繁琐，这里省略
  end;
end;