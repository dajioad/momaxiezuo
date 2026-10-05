# release.ps1 - 墨码写作一键发版
#
# [已废弃 2026-10] 本脚本依赖自有服务器 39.96.64.137（上传 + 远程签名），
# 服务器下线后不可再用。请改用图形化的 Gitee 版发版工具：
#   双击「启动发版工具.bat」（release_gui.py）
param(
    [Parameter(Mandatory=$true)][string]$Version,
    [string]$Notes = "",
    [string]$SshHost = "root@39.96.64.137",
    [string]$RemoteDir = "/var/www/moma"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 确保 PATH 里有 ssh
$env:PATH = "C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;" +
            "C:\Windows\System32\WindowsPowerShell\v1.0\;C:\Windows\System32\OpenSSH\;$env:PATH"

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[ERROR] 找不到 .venv\Scripts\python.exe" -ForegroundColor Red
    exit 1
}

# ─────────────── 可配置路径 ───────────────
# Inno Setup 6 编译器（按需修改）
$ISCC = "D:\Inno Setup 6\ISCC.exe"
# Inno Setup 脚本路径
$IssFile = Join-Path $PSScriptRoot "打包.iss"
# ──────────────────────────────────────────

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  墨码写作 - 发版 v$Version" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ─────────────────────────────────────────────────────────────
# 1. 改 ui/constants.py 的版本号
# ─────────────────────────────────────────────────────────────
Write-Host "[1/9] 改 ui/constants.py 版本号到 $Version" -ForegroundColor Cyan
& $py "_bump_version.py" $Version
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 改版本号失败" -ForegroundColor Red
    exit 1
}

# ─────────────────────────────────────────────────────────────
# 2. 改 打包.iss 的 MyAppVersion（否则安装包文件名版本号是旧的）
# ─────────────────────────────────────────────────────────────
Write-Host "[2/9] 同步 打包.iss 版本号..." -ForegroundColor Cyan
if (-not (Test-Path $IssFile)) {
    Write-Host "[ERROR] 找不到 $IssFile" -ForegroundColor Red
    exit 1
}
$issText = Get-Content $IssFile -Raw -Encoding UTF8
$issNew = $issText -replace '#define\s+MyAppVersion\s+"[^"]+"',
                            "#define MyAppVersion     `"$Version`""
if ($issNew -eq $issText) {
    Write-Host "      [WARN] iss 里的 MyAppVersion 未变更（可能格式不同）" -ForegroundColor Yellow
}
Set-Content $IssFile $issNew -Encoding UTF8 -NoNewline
Write-Host "      OK"

# ─────────────────────────────────────────────────────────────
# 3. 打包主程序
# ─────────────────────────────────────────────────────────────
Write-Host "[3/9] 打包主程序（约 3-5 分钟）..." -ForegroundColor Cyan
& $py "build.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 打包失败" -ForegroundColor Red
    exit 1
}

$distDir = Join-Path $PSScriptRoot "dist\墨码写作"
if (-not (Test-Path $distDir)) {
    Write-Host "[ERROR] 未找到打包产物：$distDir" -ForegroundColor Red
    exit 1
}

# ─────────────────────────────────────────────────────────────
# 4. 绿色包：moma-<ver>-win-x64.zip（完整，含 updater.exe）
# ─────────────────────────────────────────────────────────────
Write-Host "[4/9] 生成绿色包 moma-$Version-win-x64.zip..." -ForegroundColor Cyan
$greenZip = Join-Path $PSScriptRoot "dist\moma-$Version-win-x64.zip"
if (Test-Path $greenZip) { Remove-Item $greenZip -Force }
Compress-Archive -Path "$distDir\*" -DestinationPath $greenZip -Force
$greenSizeMB = [math]::Round((Get-Item $greenZip).Length / 1MB, 1)
Write-Host "      OK ($greenSizeMB MB)"

# ─────────────────────────────────────────────────────────────
# 5. update.zip：排除 updater.exe（自动更新用，避免覆盖自己）
# ─────────────────────────────────────────────────────────────
Write-Host "[5/9] 生成 update.zip..." -ForegroundColor Cyan
$zipPath = Join-Path $PSScriptRoot "dist\update.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

$updaterHold = Join-Path $PSScriptRoot "dist\updater.exe.hold"
Push-Location $distDir
try {
    if (Test-Path "updater.exe") {
        Move-Item "updater.exe" $updaterHold -Force
    }
    Compress-Archive -Path * -DestinationPath $zipPath -Force
} finally {
    if (Test-Path $updaterHold) {
        Move-Item $updaterHold "updater.exe" -Force
    }
    Pop-Location
}
$sizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host "      OK ($sizeMB MB)"

# ─────────────────────────────────────────────────────────────
# 6. 编译 Inno Setup 安装包
# ─────────────────────────────────────────────────────────────
Write-Host "[6/9] 编译安装包（Inno Setup）..." -ForegroundColor Cyan
if (-not (Test-Path $ISCC)) {
    Write-Host "[ERROR] 找不到 Inno Setup：$ISCC" -ForegroundColor Red
    Write-Host "        请修改 release.ps1 顶部的 `$ISCC 路径" -ForegroundColor Yellow
    exit 1
}

# 编译前先记下 installer_output 里已有的 exe（时间戳），
# 编译后通过"新增文件"定位真实产物，不依赖 iss 的输出命名规则。
$innoOutDir = Join-Path $PSScriptRoot "installer_output"
if (-not (Test-Path $innoOutDir)) {
    New-Item -ItemType Directory -Path $innoOutDir -Force | Out-Null
}
$before = @{}
Get-ChildItem $innoOutDir -Filter *.exe -ErrorAction SilentlyContinue |
    ForEach-Object { $before[$_.FullName] = $_.LastWriteTimeUtc }

& $ISCC $IssFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 安装包编译失败" -ForegroundColor Red
    exit 1
}

# 找本次编译新增（或时间戳变了）的 exe
$newExes = @()
Get-ChildItem $innoOutDir -Filter *.exe -ErrorAction SilentlyContinue |
    ForEach-Object {
        $old = $before[$_.FullName]
        if (-not $old -or $_.LastWriteTimeUtc -gt $old) {
            $newExes += $_
        }
    }

if ($newExes.Count -eq 0) {
    Write-Host "[ERROR] Inno 编译后未在 installer_output 下发现新 exe" -ForegroundColor Red
    Write-Host "        请检查 打包.iss 的 OutputDir 配置" -ForegroundColor Yellow
    exit 1
}

# 多个就取最新的那个
$installerInno = ($newExes | Sort-Object LastWriteTimeUtc -Descending)[0]
Write-Host "      Inno 输出：$($installerInno.Name)"

# 复制到 dist 并重命名为 ASCII（上传用）
$installerName = "moma-$Version-win-x64.exe"
$installerLocal = Join-Path $PSScriptRoot "dist\$installerName"
if (Test-Path $installerLocal) { Remove-Item $installerLocal -Force }
Copy-Item $installerInno.FullName $installerLocal -Force
$installerSizeMB = [math]::Round((Get-Item $installerLocal).Length / 1MB, 1)
Write-Host "      OK ($installerSizeMB MB)  →  $installerName"

# ─────────────────────────────────────────────────────────────
# 7. 算 hash（自动更新校验用，只针对 update.zip）
# ─────────────────────────────────────────────────────────────
Write-Host "[7/9] 计算 SHA-256..." -ForegroundColor Cyan
$hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLower()
Write-Host "      $hash" -ForegroundColor Yellow

# ─────────────────────────────────────────────────────────────
# 8. 上传 3 个文件
# ─────────────────────────────────────────────────────────────
Write-Host "[8/9] 上传到服务器..." -ForegroundColor Cyan

Write-Host "  → update.zip" -ForegroundColor DarkGray
scp $zipPath "${SshHost}:${RemoteDir}/downloads/update.zip"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] update.zip 上传失败" -ForegroundColor Red
    exit 1
}

Write-Host "  → $installerName" -ForegroundColor DarkGray
scp $installerLocal "${SshHost}:${RemoteDir}/downloads/$installerName"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 安装包上传失败" -ForegroundColor Red
    exit 1
}

Write-Host "  → moma-$Version-win-x64.zip" -ForegroundColor DarkGray
scp $greenZip "${SshHost}:${RemoteDir}/downloads/moma-$Version-win-x64.zip"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 绿色包上传失败" -ForegroundColor Red
    exit 1
}
Write-Host "      OK"

# 8.5 服务器上生成 latest 固定名（下载页 URL 永远不变）
Write-Host "  → 服务器生成 latest 别名" -ForegroundColor DarkGray
$latestCmds = "cd ${RemoteDir}/downloads && " +
              "cp -f moma-$Version-win-x64.exe moma-latest-win-x64.exe && " +
              "cp -f moma-$Version-win-x64.zip moma-latest-win-x64.zip && " +
              "ls -lh moma-latest-win-x64.*"
ssh $SshHost $latestCmds
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] latest 别名生成失败（不影响本次发版）" -ForegroundColor Yellow
}

# ─────────────────────────────────────────────────────────────
# 9. 服务器签名
# ─────────────────────────────────────────────────────────────
Write-Host "[9/9] 服务器签名并更新 latest.json..." -ForegroundColor Cyan
$notesBytes = [System.Text.Encoding]::UTF8.GetBytes($Notes)
$notesB64 = [System.Convert]::ToBase64String($notesBytes)

$result = ssh $SshHost "python3 /root/moma/sign.py '$Version' '$hash' '$notesB64'"
if ($result -notmatch "OK") {
    Write-Host "[ERROR] 签名失败：$result" -ForegroundColor Red
    exit 1
}
Write-Host "      OK"

# ─────────────────────────────────────────────────────────────
# 完成
# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  发版完成!  v$Version" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "SHA-256: $hash" -ForegroundColor Yellow
Write-Host ""
Write-Host "下载地址（固定名，可写死在下载页）：" -ForegroundColor Cyan
Write-Host "  安装包: http://39.96.64.137/downloads/moma-latest-win-x64.exe"
Write-Host "  绿色包: http://39.96.64.137/downloads/moma-latest-win-x64.zip"
Write-Host ""
Write-Host "本版归档（可回滚）：" -ForegroundColor Cyan
Write-Host "  安装包: http://39.96.64.137/downloads/$installerName"
Write-Host "  绿色包: http://39.96.64.137/downloads/moma-$Version-win-x64.zip"
Write-Host ""