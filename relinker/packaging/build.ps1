# P4 打包脚本（onedir）—— 一条命令出包，然后自己验收前四项
# 用法：powershell -NoProfile -File packaging\build.ps1
# 说明：
#   · 必须用 uv 的 3.12（系统 3.13 没 tkinter，打出来是废包）
#   · 必须 onedir（本机 %TEMP% 不可写，onefile 起不来）
#   · 引擎不走 --add-data（这台机器上 PowerShell 传 `源;目标` 会被 PyInstaller 判成
#     "Wrong syntax"，改用「构建完再复制进 _internal\engine\Everything」最稳）

$ErrorActionPreference = "Stop"

$PY   = "C:\Users\李\AppData\Roaming\uv\python\cpython-3.12.15-windows-x86_64-none\python.exe"
$PYI  = "C:\Users\李\Documents\deepseek-harness\default-workspace\ae-tools\pyi"
$ROOT = "C:\Users\李\Documents\deepseek-harness\default-workspace\ae-tools\rewrite\media-doctor-v2"
# 产品名与图标（2026-11-15 改名为「拾链」；新图标放 rewrite\assets\shilian.ico，
# 没生成好之前回退用一代的 doctor.ico，并在输出里明说）
$NAME = "拾链"
$ICO  = Join-Path $ROOT "..\assets\shilian.ico"
$ICO_OLD = "C:\插件\自己的插件和辅助工具\AE素材医生-源码与维修报告\assets\doctor.ico"
$ENG  = "C:\Users\李\AppData\Roaming\AE素材医生\engine\Everything"
$DIST = Join-Path $ROOT ("dist\" + $NAME)
$OUT  = "C:\插件\自己的插件和辅助工具\素材修复2"

Set-Location $ROOT
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $PYI

foreach ($p in @($PY, (Join-Path $ENG "everything.exe"), (Join-Path $ENG "es.exe"), (Join-Path $ENG "Everything.lng"))) {
  if (-not (Test-Path $p)) { throw "缺少依赖：$p" }
}
if (-not (Test-Path $ICO)) {
  Write-Host "注意：新图标 $ICO 还没生成，本次用一代的 doctor.ico 代替"
  $ICO = $ICO_OLD
}
if (-not (Test-Path $ICO)) { throw "缺少图标：$ICO" }

if (Test-Path "dist")  { Remove-Item "dist"  -Recurse -Force }
if (Test-Path "build") { Remove-Item "build" -Recurse -Force }

& $PY -m PyInstaller --noconfirm --onedir --windowed --name $NAME `
  --icon $ICO `
  --distpath (Join-Path $ROOT "dist") --workpath (Join-Path $ROOT "build") --specpath (Join-Path $ROOT "build") `
  (Join-Path $ROOT "main.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败，exit=$LASTEXITCODE" }

# 内置引擎（只三个文件：不要 Everything.ini（我们改过的）、不要 Everything.db（64 MB 运行时索引））
$engDst = Join-Path $DIST "_internal\engine\Everything"
New-Item -ItemType Directory -Path $engDst -Force | Out-Null
foreach ($f in "everything.exe", "es.exe", "Everything.lng") {
  Copy-Item (Join-Path $ENG $f) (Join-Path $engDst $f) -Force
}

# 随包文档
Copy-Item (Join-Path $ROOT "packaging\说明.txt")   (Join-Path $DIST "说明.txt")   -Force
Copy-Item (Join-Path $ROOT "packaging\先读我.txt") (Join-Path $DIST "先读我.txt") -Force

Write-Host "构建完成：$DIST"
Get-ChildItem $DIST | Select-Object Name, Length | Format-Table -AutoSize