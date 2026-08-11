# Снимок рабочего стола с демо-папками — для README проекта.
#
# ЗАПУСКАТЬ ОТ ИМЕНИ АДМИНИСТРАТОРА:
#   правой кнопкой по файлу -> «Выполнить с помощью PowerShell»
#   (если не предложит повышение прав, скрипт попросит сам)
#
# Права нужны ровно для одного: временно скрыть ярлыки из общей папки
# C:\Users\Public\Desktop. Всё остальное — личные папки, Корзина, раскладка —
# делается без них.
#
# Что произойдёт:
#   1. скроются ярлыки из общей папки и все твои личные папки на столе
#      (атрибут «скрытый»; ни один файл никуда не перемещается);
#   2. на стол лягут 50 выдуманных папок из demo/desktop;
#   3. они разложатся по смыслу, экран снимется в docs/desktop.png;
#   4. всё вернётся как было.
#
# Возврат идёт в блоке finally: даже если что-то упадёт посередине,
# атрибуты снимутся и демо-папки удалятся.

$ErrorActionPreference = 'Stop'

# --- повышение прав, если запустили обычным пользователем ---
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "Нужны права администратора — запрашиваю..."
    $self = $PSCommandPath
    Start-Process powershell -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $self
    )
    exit
}

$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# Всё окно пишем в файл: если что-то пойдёт не так, будет что показать.
New-Item -ItemType Directory -Force (Join-Path $root 'logs') | Out-Null
try { Start-Transcript -Path (Join-Path $root 'logs\shot_ps.log') -Append | Out-Null } catch {}

Write-Host "Проект: $root"

# --- интерпретатор ---
$py = $null
foreach ($c in @(
    "$root\.venv\Scripts\python.exe",
    "$root\..\соц аналитик\socanalytic\venv\Scripts\python.exe"
)) { if (Test-Path $c) { $py = (Resolve-Path $c).Path; break } }
if (-not $py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source }
}
if (-not $py) { Write-Host "Не нашёл Python."; Read-Host "Enter"; exit 1 }
Write-Host "Python: $py"

$pub = Join-Path $env:PUBLIC 'Desktop'
$hidden = @()

try {
    # --- скрываем ярлыки из общей папки ---
    Get-ChildItem $pub -Force | Where-Object { $_.Name -ne 'desktop.ini' } | ForEach-Object {
        if (-not ($_.Attributes -band [IO.FileAttributes]::Hidden)) {
            $_.Attributes = $_.Attributes -bor [IO.FileAttributes]::Hidden
            $hidden += $_.FullName
        }
    }
    Write-Host "Скрыто ярлыков из общей папки: $($hidden.Count)"

    # --- сторож не должен мешать ---
    Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
    Remove-Item "$root\logs\watch.lock" -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1

    # --- сам снимок (личные папки прячет уже python) ---
    $env:PYTHONIOENCODING = 'utf-8'
    & $py "$root\demo\make_desktop_shot.py"
}
finally {
    Write-Host ""
    Write-Host "Возвращаю ярлыки общей папки..."
    foreach ($p in $hidden) {
        try {
            $i = Get-Item $p -Force
            $i.Attributes = $i.Attributes -band (-bnot [IO.FileAttributes]::Hidden)
        } catch { Write-Host "  не вышло: $p" }
    }
    Write-Host "Возвращено: $($hidden.Count)"

    # --- раскладка и фоновые процессы обратно ---
    & $py "$root\desktop_icons.py" spacing 115 123 | Out-Null
    & $py "$root\desktop_icons.py" apply "$root\data\layout.json" | Out-Null
    $vbs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\DesktopAtlas.vbs'
    if (Test-Path $vbs) { Start-Process wscript.exe -ArgumentList $vbs }
    Write-Host "Раскладка и фоновые процессы восстановлены."
}

Write-Host ""
$shot = Join-Path $root 'docs\desktop.png'
if (Test-Path $shot) {
    $kb = [math]::Round((Get-Item $shot).Length / 1KB)
    Write-Host "Готово. Снимок: $shot ($kb КБ)"
} else {
    Write-Host "СНИМОК НЕ СОЗДАН. Что случилось — в logs\shot.log и logs\shot_ps.log"
}
try { Stop-Transcript | Out-Null } catch {}
Read-Host "Нажми Enter, чтобы закрыть"
