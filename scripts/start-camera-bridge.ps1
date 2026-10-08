# SENTINEL-X — démarre le pont caméra natif (webcam USB externe -> conteneur d'IA)
#   powershell -ExecutionPolicy Bypass -File scripts\start-camera-bridge.ps1            # démarre en arrière-plan
#   powershell -ExecutionPolicy Bypass -File scripts\start-camera-bridge.ps1 -Stop      # arrête
#   powershell -ExecutionPolicy Bypass -File scripts\start-camera-bridge.ps1 -Autostart # lance à chaque ouverture de session
param([switch]$Stop, [switch]$Autostart)
$ErrorActionPreference = "Stop"

$root   = Split-Path -Parent $PSScriptRoot
$dir    = Join-Path $root "ai-engine\camera-bridge"
$venv   = Join-Path $dir ".venv"
$py     = Join-Path $venv "Scripts\pythonw.exe"
$log    = Join-Path $dir "bridge.log"
$port   = 8554

function Get-BridgeProcess {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue }
}

if ($Stop) {
  Get-BridgeProcess | Stop-Process -Force
  Write-Host "Pont caméra arrêté."
  return
}

if (-not (Test-Path (Join-Path $root "secrets\camera_token"))) {
  throw "secrets\camera_token absent : lancez d'abord  bash scripts/setup.sh"
}

# environnement Python isolé (créé une seule fois)
if (-not (Test-Path $py)) {
  $base = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (-not $base -or $base -like "*WindowsApps*") {
    $base = Get-ChildItem "$env:APPDATA\uv\python\cpython-3.1*\python.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
  }
  if (-not $base) { throw "Python 3.11+ introuvable : installez-le (winget install Python.Python.3.12)" }
  & $base -m venv $venv
  & (Join-Path $venv "Scripts\python.exe") -m pip install --quiet --disable-pip-version-check -r (Join-Path $dir "requirements.txt")
}

if (Get-BridgeProcess) {
  Write-Host "Pont caméra déjà démarré (port $port)."
} else {
  # pythonw : pas de fenêtre ; journal dans ai-engine\camera-bridge\bridge.log
  $cmd = "import runpy, sys; sys.stdout = sys.stderr = open(r'$log', 'a', buffering=1, encoding='utf-8'); runpy.run_path(r'$(Join-Path $dir 'bridge.py')', run_name='__main__')"
  Start-Process -FilePath $py -ArgumentList "-c", "`"$cmd`"" -WorkingDirectory $dir -WindowStyle Hidden
  for ($i = 0; $i -lt 20 -and -not (Get-BridgeProcess); $i++) { Start-Sleep 1 }
  if (Get-BridgeProcess) { Write-Host "Pont caméra démarré sur 127.0.0.1:$port (journal : $log)" }
  else { Get-Content $log -Tail 20; throw "Le pont caméra n'a pas démarré" }
}

if ($Autostart) {
  $startup = [Environment]::GetFolderPath("Startup")
  $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $startup "Sentinel-X camera bridge.lnk"))
  $lnk.TargetPath = "powershell.exe"
  $lnk.Arguments = "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`""
  $lnk.Save()
  Write-Host "Démarrage automatique activé (dossier Démarrage de Windows)."
}
