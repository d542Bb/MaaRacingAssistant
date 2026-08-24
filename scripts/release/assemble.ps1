# Release package assembly for MRA Windows.
# Output: <OutRoot>/MaaRacingAssistant-<Version>-win-x64.zip + .sha256
# Structure reproduces the validated local package:
#   <name>/{ mra_shell.exe + WinUI/.NET dlls, pyproject.toml,
#          maaracing_assistant/, assets/ (含 config/), apps/mra_shell/frontend/,
#          runtime/python/{python.exe, python311._pth, packages/, vcruntime140*.dll} }
# Usage: powershell -File assemble.ps1 -Version 1.0.0
#   -HostPython       host interpreter, MUST be 3.11 (cp311) to match embedded
#   -SourceRuntimeDir reuse an existing verified runtime (skip download+pip) for local re-verify
#   -KeepGoing        continue on error (local debug); default fail-fast

param(
    [Parameter(Mandatory=$true)][string]$Version,
    [string]$RepoRoot = '',
    [string]$OutRoot = '',
    [string]$EmbedPythonUrl = 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip',
    [string]$LockFile = '',
    [string]$HostPython = 'python',
    [string]$SourceRuntimeDir = '',
    [string]$RuntimeCacheDir = '',
    [string]$VcVarsAll = '',   # native Launcher 编译用 MSVC vcvarsall.bat 路径（默认自动探测）
    [switch]$SkipPublish,
    [switch]$KeepGoing
)

$ErrorActionPreference = 'Stop'

if (-not $RepoRoot) { $RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
if (-not $OutRoot)  { $OutRoot  = $PSScriptRoot }
if (-not $LockFile) { $LockFile = Join-Path $PSScriptRoot 'requirements-runtime-lock.txt' }

$RepoRoot  = (Resolve-Path $RepoRoot).Path
if (-not (Test-Path $OutRoot)) { New-Item -ItemType Directory -Force -Path $OutRoot | Out-Null }
$OutRoot   = (Resolve-Path $OutRoot).Path
if (-not $RuntimeCacheDir) { $RuntimeCacheDir = Join-Path $RepoRoot 'build\runtime-cache' }
# native Launcher 编译需 MSVC；未显式指定时自动探测常见 VS 安装路径
if (-not $VcVarsAll) {
    $candidates = @(
        'C:\Program Files\Microsoft Visual Studio\2022\Enterprise',
        'C:\Program Files\Microsoft Visual Studio\2022\Professional',
        'C:\Program Files\Microsoft Visual Studio\2022\Community',
        'C:\Program Files\Microsoft Visual Studio\2022\BuildTools',
        'C:\Program Files (x86)\Microsoft Visual Studio\2019\Enterprise',
        'C:\Program Files (x86)\Microsoft Visual Studio\2019\Professional',
        'C:\Program Files\Microsoft Visual Studio\2019\Community',
        'C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools',
        'D:\Microsoft Visual Studio\2019\Community',
        'D:\Microsoft Visual Studio\2022\Community'
    )
    $VcVarsAll = $candidates |
        ForEach-Object { $p = Join-Path $_ 'VC\Auxiliary\Build\vcvarsall.bat'; if (Test-Path $p) { $p } } |
        Select-Object -First 1
}
$Name      = 'MaaRacingAssistant-' + $Version + '-win-x64'
$StageRoot = Join-Path $OutRoot $Name

Write-Host "[assemble] Version=$Version stage=$StageRoot"

$errors = New-Object System.Collections.Generic.List[string]
$Fail = { param($m) Write-Host "FATAL: $m" -ForegroundColor Red; exit 1 }

# ---------- 缓存源指纹（防跨分支 / 改代码后误用过期缓存） ----------
$pubFinger = 'publish/' + ((Get-ChildItem (Join-Path $RepoRoot 'apps\mra_shell') -Recurse -File |
    Where-Object { $_.Extension -in '.cs','.xaml','.csproj','.manifest' -and $_.FullName -notmatch '\\(bin|obj)\\' } |
    Sort-Object FullName |
    ForEach-Object { (Get-FileHash $_.FullName -Algorithm SHA256).Hash }) -join ';')
$lockHash = (Get-FileHash $LockFile -Algorithm SHA256).Hash
$pyMinor  = (& $HostPython -c "import sys;print(sys.version_info[0],'.',sys.version_info[1])" 2>$null) -join ''
$rtFinger = 'runtime/' + $lockHash + '/' + $pyMinor
# 读取/写入缓存目录内的指纹标记
$CacheFPWrite = { param($dir,$finger) Set-Content -Path (Join-Path $dir '.cache-fingerprint') -Value $finger -Encoding ascii }
$CacheFPMatch = { param($dir,$finger) $f = Join-Path $dir '.cache-fingerprint'; if (Test-Path $f) { return ((Get-Content $f -Raw).Trim() -eq $finger) }; return $false }

# ---------- 0. stage ----------
if (Test-Path $StageRoot) { Remove-Item $StageRoot -Recurse -Force }
New-Item -ItemType Directory -Path $StageRoot | Out-Null

# ---------- 1. runtime ----------
$rtDir = Join-Path $StageRoot 'runtime\python'
# 复用已验证 runtime：显式 -SourceRuntimeDir 优先；其次仅当 build/runtime-cache
# 指纹（lock 文件 + host python）匹配时才复用，否则全量下载 + 组装后写回缓存。
$rtSource = $null
if ($SourceRuntimeDir) {
    $rtSource = (Resolve-Path $SourceRuntimeDir).Path
} elseif ((Test-Path $RuntimeCacheDir) -and (& $CacheFPMatch $RuntimeCacheDir $rtFinger)) {
    $rtSource = (Resolve-Path $RuntimeCacheDir).Path
}
if ($rtSource) {
    New-Item -ItemType Directory -Path $rtDir | Out-Null
    Copy-Item (Join-Path $rtSource '*') $rtDir -Recurse -Force
    Write-Host "[assemble] 复用已缓存 runtime: $rtSource (跳过下载+pip)"
} else {
    New-Item -ItemType Directory -Path $rtDir | Out-Null
    $embedZip = Join-Path $env:TEMP 'python-embed-3119.zip'
    if (-not (Test-Path $embedZip)) {
        # 加超时，避免网络卡死时无限挂起（正常下载 10MB 内）
        Invoke-WebRequest -Uri $EmbedPythonUrl -OutFile $embedZip -TimeoutSec 120
    }
    Set-Location $rtDir
    tar -xf $embedZip
    Set-Location $OutRoot

    $packages = Join-Path $rtDir 'packages'
    New-Item -ItemType Directory -Path $packages | Out-Null
    # 去掉 -q：逐包实时输出便于定位卡点；--progress-bar off 避免刷屏；保留超时/重试防无限挂
    # --find-links 优先用 scripts/release/wheels 内预构建 wheel（vgamepad 的 sdist 在 CI 卡 Preparing metadata，改走本地 wheel）
    & $HostPython -m pip install --disable-pip-version-check --no-warn-script-location --progress-bar off --timeout 60 --retries 2 --find-links (Join-Path $RepoRoot 'scripts\release\wheels') --target $packages -r $LockFile
    if ($LASTEXITCODE -ne 0) { $errors.Add('pip install runtime deps failed (host python must be 3.11/cp311)') }

    foreach ($dll in @('vcruntime140.dll', 'vcruntime140_1.dll')) {
        $sys = Join-Path $env:WinDir ('System32\' + $dll)
        if (Test-Path $sys) { Copy-Item $sys (Join-Path $rtDir $dll) -Force }
    }
}

$pthContent = "python311.zip`n.`npackages`n..\..`n# repo root on sys.path; sidecar started with -m`nimport site"
Set-Content -Path (Join-Path $rtDir 'python311._pth') -Value $pthContent -Encoding ascii

# ---------- 2. dotnet publish ----------
# 统一输出到 build/publish-cache 并记录 C#/XAML 源指纹；
# -SkipPublish 时仅当指纹一致才复用（改过源码会自动重编，杜绝跨分支误用旧产物）。
# PoC 布局：GUI publish 整目录进入 StageRoot\app\（实现目录），根目录只保留 Launcher 与资源。
$publishDir = Join-Path $RepoRoot 'build\publish-cache'
$csproj = Join-Path $RepoRoot 'apps\mra_shell\mra_shell.csproj'
if ($SkipPublish -and (& $CacheFPMatch $publishDir $pubFinger)) {
    Write-Host '[assemble] 复用已编译 GUI（-SkipPublish，指纹一致）'
} else {
    if (Test-Path $publishDir) { Remove-Item $publishDir -Recurse -Force }
    New-Item -ItemType Directory -Path $publishDir | Out-Null
    & dotnet publish $csproj -c Release -r win-x64 --self-contained true -p:Version=$Version -o $publishDir | Out-Null
    if ($LASTEXITCODE -ne 0) { $errors.Add('dotnet publish failed') }
    & $CacheFPWrite $publishDir $pubFinger
}

# 复制 GUI 产物到 StageRoot\app\
$appDir = Join-Path $StageRoot 'app'
Get-ChildItem $publishDir -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($publishDir.Length).TrimStart('\')
    $dest = Join-Path $appDir $rel
    New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
    Copy-Item $_.FullName $dest -Force
}

# ---------- 2.5 publish 产物瘦身（作用于 StageRoot\app\）----------
# 1) *.exe.WebView2\EBWebView 是 WebView2 运行时生成的用户缓存
#    （Cookies/History/GPUCache/CodeCache 等），仅本机运行残留，纯冗余且带隐私，
#    用户机器首次运行会自动重建，删除无副作用。
# 2) 语言包目录仅保留 zh-CN：WindowsAppSDK 每个 self-contained publish 都会把
#    全量多语言 .mui 复制进来（几十个目录占用大量小文件），本项目只面向中文。
$cleanDir = Join-Path $StageRoot 'app'
$webview2Dirs = Get-ChildItem $cleanDir -Directory -Filter '*.exe.WebView2' -Recurse -ErrorAction SilentlyContinue
foreach ($d in $webview2Dirs) {
    Remove-Item $d.FullName -Recurse -Force
    Write-Host "[assemble] 清理 WebView2 用户缓存: $($d.Name)"
}
$langDirs = Get-ChildItem $cleanDir -Directory | Where-Object {
    $_.Name -ne 'zh-CN' -and (Get-ChildItem $_.FullName -Filter '*.mui' -ErrorAction SilentlyContinue | Select-Object -First 1)
}
foreach ($d in $langDirs) {
    Remove-Item $d.FullName -Recurse -Force
    Write-Host "[assemble] 清理语言包: $($d.Name)"
}

# ---------- 2.6 native Launcher 编译 ----------
# 根目录唯一入口 MaaRacingAssistant.exe（薄 Launcher，见 apps/mra_launcher/launcher.c）。
# 用 MSVC 编译为静态链接（/MT）GUI 子系统 exe，零外部 runtime 依赖；每次重新编译（体积小、快），不缓存。
# 现在通过 launcher.rc 一次性嵌入两类 Win32 资源：图标（resources/icon）与内嵌申请清单
# （launcher.manifest 的 requireAdministrator 提权）。作用：① 入口 exe 有图标；
# ② Launcher 启动即申请管理员权限，CreateProcessW 启动 mra_shell.exe 时子进程继承
# 同一 token，规避 ERROR_ELEVATION_REQUIRED (740)。
# 优先直接用 PATH 里的 cl/rc（CI 用 setup-msvc 已配置环境）；否则自动探测 vcvarsall.bat 初始化。
$launcherSrc = Join-Path $RepoRoot 'apps\mra_launcher\launcher.c'
$launcherOut = Join-Path $StageRoot 'MaaRacingAssistant.exe'
$launcherObj = Join-Path $env:TEMP 'mra_launcher.obj'
# rc 内部的相对资源路径（..\..\assets\icon.ico、launcher.manifest）是按编译时的工作目录
# （而非 .rc 文件所在目录）解析的，因此编译前必须把 cwd 切到 apps\mra_launcher 并保持到结束。
# res 以相对名下发当前目录生成：rc.exe 对带引号的 /fo 及环境变量路径存在 RC1109 坑，
# 无引号相对名最稳；编译后立即删除该临时 res，避免污染源码目录。
$launcherDir = Join-Path $RepoRoot 'apps\mra_launcher'
$launcherRes = Join-Path $launcherDir 'mra_launcher.res'
Push-Location $launcherDir
try {
    $clInPath = (Get-Command cl.exe -ErrorAction SilentlyContinue)
    if ($clInPath -and (Test-Path -Path $launcherSrc)) {
        Write-Host "[assemble] 用 PATH 中的 cl.exe/rc.exe 编译 Launcher"
        & rc.exe /nologo /fomra_launcher.res launcher.rc
        if ($LASTEXITCODE -ne 0) { $errors.Add('Launcher 资源编译失败 (rc.exe 退出码 ' + $LASTEXITCODE + ')') }
        & cl.exe /nologo /utf-8 /O2 /MT /Fe:"$launcherOut" /Fo:"$launcherObj" launcher.c mra_launcher.res /link /SUBSYSTEM:WINDOWS user32.lib shell32.lib | Out-Null
        Remove-Item $launcherRes -Force -ErrorAction SilentlyContinue
        if ($LASTEXITCODE -ne 0) { $errors.Add('Launcher 编译失败 (cl.exe 退出码 ' + $LASTEXITCODE + ')') }
    } elseif (-not $VcVarsAll) {
        $errors.Add('未找到 MSVC (cl.exe 不在 PATH，也无 vcvarsall.bat)；无法编译 native Launcher。用 -VcVarsAll 指定路径，或 CI 先 setup-msvc')
    } elseif (-not (Test-Path -Path $launcherSrc)) {
        $errors.Add('missing launcher source: ' + $launcherSrc)
    } else {
        Write-Host "[assemble] 用 vcvarsall 编译 Launcher: $(Split-Path $launcherOut -Leaf)"
        # cmd /c 内 call vcvarsall 一次性生效（PowerShell 无法直接继承批处理环境）
        $cmdLine = "`"$VcVarsAll`" x64 >nul 2>&1 && rc.exe /nologo /fomra_launcher.res launcher.rc && cl.exe /nologo /utf-8 /O2 /MT /Fe:`"$launcherOut`" /Fo:`"$launcherObj`" launcher.c mra_launcher.res /link /SUBSYSTEM:WINDOWS user32.lib shell32.lib & del /q /s mra_launcher.res >nul 2>&1"
        cmd /c $cmdLine | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $errors.Add('Launcher 编译失败 (cl.exe 退出码 ' + $LASTEXITCODE + ')')
        }
    }
} finally {
    Pop-Location
}
if (-not (Test-Path $launcherOut)) {
    $errors.Add('Launcher 编译后未生成: ' + $launcherOut)
} else {
    Write-Host "[assemble] Launcher OK: $launcherOut"
}
# Launcher 前置校验：app\mra_shell.exe 必须存在（Launcher 依赖它启动）
if (-not (Test-Path (Join-Path $appDir 'mra_shell.exe'))) {
    $errors.Add('app\mra_shell.exe not found — Launcher 无法启动 GUI')
}

# ---------- 3. whitelist ----------
foreach ($rel in @(
    'pyproject.toml', 'LICENSE', 'THIRD_PARTY_LICENSES.md',
    'assets\model', 'assets\icon.ico',
    'assets\mra_icon.png', 'assets\config', 'apps\mra_shell\frontend')) {
    $src = Join-Path $RepoRoot $rel
    if (Test-Path $src) {
        $dest = Join-Path $StageRoot $rel
        New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
        Copy-Item $src $dest -Recurse -Force
    } else { $errors.Add('missing source path: ' + $rel) }
}
robocopy (Join-Path $RepoRoot 'maaracing_assistant') (Join-Path $StageRoot 'maaracing_assistant') /E /XD __pycache__ /XJ /NFL /NDL /NJH /NJS /NP | Out-Null

# ---------- 4. _version.py ----------
$verContent = @(
    '# file generated for release (no VCS)',
    'from __future__ import annotations',
    ('__version__ = version = "{0}"' -f $Version)
)
Set-Content -Path (Join-Path $StageRoot 'maaracing_assistant\_version.py') -Value $verContent -Encoding ascii

# ---------- 5. import self-check ----------
# vgamepad 单独容错：import 即建 ViGEmBus VBus()，无驱动环境（CI runner）会抛
# VIGEM_ERROR_BUS_NOT_FOUND。这证明 wheel 已正确装载，只是缺系统驱动，属于运行
# 环境问题而非打包问题（用户机器装了 ViGEmBus 驱动后即可正常使用），降级为 warning。
$py = Join-Path $rtDir 'python.exe'
$checkCode = @'
import sys
sys.path.insert(0, sys.argv[1])
for m in ('maa', 'onnxruntime', 'cv2', 'numpy', 'rapidocr', 'windows_capture'):
    __import__(m)
import maaracing_assistant
print(maaracing_assistant.__version__)
try:
    __import__('vgamepad')
except Exception as e:
    if 'VIGEM_ERROR_BUS_NOT_FOUND' in str(e):
        print('[warn] vgamepad installed but ViGEmBus driver missing; virtual gamepad unavailable (runtime env limit)')
    else:
        raise
'@
& $py -c $checkCode $StageRoot
if ($LASTEXITCODE -ne 0) { $errors.Add('import self-check failed') }

# 自检通过且本次为全新构建（未复用缓存/源码 runtime）→ 写回 build/runtime-cache（含指纹），供下次指纹匹配时复用
if ($LASTEXITCODE -eq 0 -and -not $rtSource) {
    New-Item -ItemType Directory -Path (Split-Path $RuntimeCacheDir -Parent) -Force | Out-Null
    if (Test-Path $RuntimeCacheDir) { Remove-Item $RuntimeCacheDir -Recurse -Force }
    Copy-Item $rtDir $RuntimeCacheDir -Recurse -Force
    & $CacheFPWrite $RuntimeCacheDir $rtFinger
    Write-Host "[assemble] runtime 已写入缓存: $RuntimeCacheDir"
}

# ---------- 6. errors ----------
if ($errors.Count -gt 0) {
    Write-Host ("[assemble] {0} error(s):" -f $errors.Count) -ForegroundColor Red
    foreach ($e in $errors) { Write-Host ('  - ' + $e) -ForegroundColor Red }
    if (-not $KeepGoing) { exit 1 }
}

# ---------- 7. zip + sha256 ----------
$zip = Join-Path $OutRoot ($Name + '.zip')
if (Test-Path $zip) { Remove-Item $zip -Force }
# 只用 $Name 目录的内容作 zip 顶层（而非再包一层 $Name 目录），
# 避免用户"解压到文件名文件夹"时目录多做一层嵌套。解压后 MaaRacingAssistant.exe 直接在解压根。
& tar -a -cf $zip -C (Join-Path $OutRoot $Name) .
if ($LASTEXITCODE -ne 0) { exit 1 }
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
$shaLine = $hash + '  ' + (Split-Path $zip -Leaf)
Set-Content -Path ($zip + '.sha256') -Value $shaLine -Encoding ascii
Write-Host ("[assemble] done: {0}  {1}" -f $zip, $hash) -ForegroundColor Green