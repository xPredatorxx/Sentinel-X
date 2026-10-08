#Requires -RunAsAdministrator
# SENTINEL-X — PC « point d'accès » : réseau de table Wi-Fi 192.168.137.0/24 via le point d'accès mobile Windows.
#
#   powershell -ExecutionPolicy Bypass -File scripts\ap-hotspot.ps1 -WithServer   # PC point d'accès ET serveur (admin)
#   powershell -ExecutionPolicy Bypass -File scripts\ap-hotspot.ps1               # PC point d'accès seul (admin)
#   powershell -ExecutionPolicy Bypass -File scripts\ap-hotspot.ps1 -Stop         # arrête le point d'accès
#   powershell -ExecutionPolicy Bypass -File scripts\ap-hotspot.ps1 -Undo         # rétablit la configuration Windows d'origine
#
# Ce que fait le script :
#   - SSID dédié, WPA2-PSK (AES), bande 2,4 GHz (seule bande de l'ESP32), mot de passe aléatoire fort ;
#   - passerelle 192.168.137.1 / réseau 192.168.137.0/24 (imposé par Windows 11 au point d'accès mobile) ;
#   - le point d'accès ne se coupe plus en l'absence de clients ;
#   - pare-feu de ce PC, pour tout ce qui vient du réseau de table : DHCP (67/68) et DNS (53) autorisés,
#     plus 443 (HTTPS), 80 (redirection HTTPS) et 8883 (MQTTS) avec -WithServer ; TOUT le reste est bloqué
#     (règles de blocage, prioritaires sur les autorisations « tous ports » de Docker Desktop).
# Limite connue : le filtrage ENTRE clients du point d'accès n'est pas possible avec Windows.
param(
  [string]$Ssid = "SENTINEL-X",
  [string]$Gateway = "192.168.137.1",
  [switch]$WithServer,
  [switch]$Stop,
  [switch]$Undo
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$group = "SENTINEL-X AP"
$tablenet = ($Gateway -replace '\.\d+$', '.0') + "/24"

# --- accès WinRT au point d'accès mobile ---------------------------------------------
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
$null = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
$ext = [System.WindowsRuntimeSystemExtensions].GetMethods()
$asTaskOp = ($ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTaskAct = ($ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
function Wait-Op($op, $type) { $t = $asTaskOp.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
function Wait-Act($act) { $t = $asTaskAct.Invoke($null, @($act)); $t.Wait(-1) | Out-Null }
$resultType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]

function Get-Tethering {
  $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
  if (-not $profile) { throw "Aucune connexion réseau source : connectez d'abord ce PC à un réseau (Wi-Fi ou Ethernet)." }
  [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
}

function Stop-Hotspot($tm) {
  if ($tm.TetheringOperationalState -eq 'On') { Wait-Op ($tm.StopTetheringAsync()) $resultType | Out-Null }
}

$ics = "HKLM:\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters"
$icssvc = "HKLM:\SYSTEM\CurrentControlSet\Services\icssvc\Settings"

if ($Stop) { Stop-Hotspot (Get-Tethering); Write-Host "Point d'accès arrêté."; return }

if ($Undo) {
  Stop-Hotspot (Get-Tethering)
  Remove-ItemProperty $ics -Name ScopeAddress, ScopeAddressBackup -ErrorAction SilentlyContinue
  Remove-ItemProperty $icssvc -Name PeerlessTimeoutEnabled -ErrorAction SilentlyContinue
  Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
  Restart-Service SharedAccess -Force -ErrorAction SilentlyContinue
  Write-Host "Configuration d'origine rétablie (réseau 192.168.137.0/24, règles $group supprimées)."
  return
}

# --- 1. plan d'adressage ------------------------------------------------------------------
# Windows 11 (24H2/25H2) impose au point d'accès mobile le réseau 192.168.137.0/24 (passerelle
# 192.168.137.1) : la valeur ScopeAddress du partage de connexion est ignorée. Le plan d'adressage
# du projet est donc décliné sur ce réseau. On retire une éventuelle ScopeAddress devenue inutile.
$tm = Get-Tethering
Stop-Hotspot $tm
Remove-ItemProperty $ics -Name ScopeAddress, ScopeAddressBackup -ErrorAction SilentlyContinue
# La clé icssvc\Settings appartient au service du point d'accès (il y range sa configuration, avec
# des droits d'écriture pour NT SERVICE\icssvc) : le script ne la crée ni ne la recrée jamais.
if (Test-Path $icssvc) {
  Set-ItemProperty $icssvc -Name PeerlessTimeoutEnabled -Value 0 -Type DWord   # ne pas couper sans client
}

# Une carte Wi-Fi n'a qu'une radio : si la connexion source est en 5 GHz et le point d'accès en
# 2,4 GHz, la radio alterne entre les deux bandes et l'ESP32 est régulièrement déconnecté.
# On demande donc à la carte de privilégier le 2,4 GHz pour sa propre connexion (même canal).
$wifi = Get-NetAdapter -Physical | Where-Object { $_.NdisPhysicalMedium -eq 9 } | Select-Object -First 1
$band = if ($wifi) { Get-NetAdapterAdvancedProperty -Name $wifi.Name -RegistryKeyword RoamingPreferredBandType -ErrorAction SilentlyContinue }
if ($band -and $band.RegistryValue -ne "1") {
  Set-NetAdapterAdvancedProperty -Name $wifi.Name -RegistryKeyword RoamingPreferredBandType -RegistryValue 1   # 1 = préférer 2,4 GHz
  # la carte redémarre : on attend le retour de la connexion source avant de relancer le point d'accès
  for ($i = 0; $i -lt 30 -and -not [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile(); $i++) { Start-Sleep 2 }
  Start-Sleep 5
}

# --- 2. Wi-Fi : SSID, WPA2-PSK AES, 2,4 GHz, mot de passe fort ----------------------------
$pwFile = Join-Path $root "secrets\wifi_hotspot_password"
if (-not (Test-Path $pwFile)) {
  New-Item -ItemType Directory -Force (Split-Path $pwFile) | Out-Null
  $chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
  $bytes = New-Object byte[] 20
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  [IO.File]::WriteAllText($pwFile, (-join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })))
}
$password = (Get-Content $pwFile -Raw).Trim()

$tm = Get-Tethering
try { $cfg = $tm.GetCurrentAccessPointConfiguration() }
catch { $cfg = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration]::new() }   # configuration absente : on en crée une
$cfg.Ssid = $Ssid
$cfg.Passphrase = $password
$cfg.Band = [Windows.Networking.NetworkOperators.TetheringWiFiBand]::TwoPointFourGigahertz
try {
  # Windows 11 : forcer WPA2 (l'ESP32 et le cahier des charges : WPA2-PSK AES)
  $cfg.AuthenticationKind = [Windows.Networking.NetworkOperators.TetheringWiFiAuthenticationKind]::Wpa2
} catch { }
Wait-Act ($tm.ConfigureAccessPointAsync($cfg))
$r = Wait-Op ($tm.StartTetheringAsync()) $resultType
if ("$($r.Status)" -ne "Success") { throw "Démarrage du point d'accès impossible : $($r.Status) $($r.AdditionalErrorMessage)" }

# --- 3. pare-feu du PC point d'accès ---------------------------------------------------------
# Règles de blocage (prioritaires sur toute autre règle) pour tout ce qui vient du réseau de table,
# sauf DHCP (UDP 67/68) et DNS (53) fournis par le partage de connexion, et — si ce PC est aussi le
# serveur — HTTPS 443, redirection 80 et MQTTS 8883 (tous les autres ports restent fermés).
Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Set-NetFirewallProfile -Profile Domain, Private, Public -Enabled True -DefaultInboundAction Block
$tcpBlocked = if ($WithServer) { "1-52", "54-79", "81-442", "444-8882", "8884-65535" } else { "1-52", "54-65535" }
New-NetFirewallRule -Group $group -DisplayName "SENTINEL-X AP - bloque TCP depuis la table" -Direction Inbound `
  -Action Block -Protocol TCP -LocalPort $tcpBlocked -RemoteAddress $tablenet | Out-Null
if ($WithServer) {
  New-NetFirewallRule -Group $group -DisplayName "SENTINEL-X - serveur (443, 80, 8883) depuis la table" -Direction Inbound `
    -Action Allow -Protocol TCP -LocalPort 443, 80, 8883 -RemoteAddress $tablenet | Out-Null
}
New-NetFirewallRule -Group $group -DisplayName "SENTINEL-X AP - bloque UDP depuis la table" -Direction Inbound `
  -Action Block -Protocol UDP -LocalPort "1-52", "54-66", "69-65535" -RemoteAddress $tablenet | Out-Null
New-NetFirewallRule -Group $group -DisplayName "SENTINEL-X AP - bloque ICMP depuis la table" -Direction Inbound `
  -Action Block -Protocol ICMPv4 -RemoteAddress $tablenet | Out-Null

Start-Sleep 3
$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object IPAddress -eq $Gateway).InterfaceAlias
Write-Host "Point d'accès actif : SSID $($cfg.Ssid) | bande $($cfg.Band) | état $($tm.TetheringOperationalState) | passerelle $Gateway sur « $ip »"
Write-Host "Mot de passe Wi-Fi : secrets\wifi_hotspot_password"
