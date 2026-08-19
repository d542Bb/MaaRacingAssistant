# Release package assembly for MRA Windows.
# Output: <OutRoot>/MaaRacingAssistant-<Version>-win-x64.zip + .sha256
# Structure reproduces the validated local package:
#   <name>/{ mra_shell.exe + WinUI/.NET dlls, pyproject.toml,
#          maaracing_assistant/, assets/, config/, apps/mra_shell/frontend/,
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

Get-ChildItem $publishDir -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($publishDir.Length).TrimStart('\')
    $dest = Join-Path $StageRoot $rel
    New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
    Copy-Item $_.FullName $dest -Force
}

# ---------- 3. whitelist ----------
foreach ($rel in @(
    'pyproject.toml', 'LICENSE', 'THIRD_PARTY_LICENSES.md',
    'assets\model', 'assets\resource', 'assets\icon.ico',
    'assets\mra_icon.png', 'config', 'apps\mra_shell\frontend')) {
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
& tar -a -cf $zip -C $OutRoot $Name
if ($LASTEXITCODE -ne 0) { exit 1 }
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
$shaLine = $hash + '  ' + (Split-Path $zip -Leaf)
Set-Content -Path ($zip + '.sha256') -Value $shaLine -Encoding ascii
Write-Host ("[assemble] done: {0}  {1}" -f $zip, $hash) -ForegroundColor Green