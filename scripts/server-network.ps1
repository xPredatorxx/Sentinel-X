#Requires -RunAsAdministrator
# SENTINEL-X — PC SERVEUR : connexion au réseau de table et pare-feu (à lancer en administrateur).
#
#   powershell -ExecutionPolicy Bypass -File scripts\server-network.ps1 -WifiPassword <mot de passe du point d'accès>
#   powershell -ExecutionPolicy Bypass -File scripts\server-network.ps1 -Undo      # revenir en DHCP, retirer les règles
#
# Ce que fait le script :
#   1. connecte la carte Wi-Fi au réseau de table (SSID SENTINEL-X, WPA2-PSK AES) ;
#   2. fixe l'adresse du serveur : 192.168.137.10/24, passerelle et DNS 192.168.137.1 (plan d'adressage) ;
#   3. pare-feu Windows « refus par défaut » : en entrée, seuls 443 (HTTPS), 80 (redirection HTTPS)
#      et 8883 (MQTTS) sont autorisés, et uniquement depuis 192.168.137.0/24 ; les règles Docker Desktop
#      qui autorisent tous les ports sont désactivées (réactivées par -Undo) ;
#   4. inscrit BIND_IP=192.168.137.10 dans .env : Docker ne publie ses ports que sur cette interface.
param(
  [string]$Ssid = "SENTINEL-X",
  [string]$WifiPassword = "",
  [string]$Ip = "192.168.137.10",
  [string]$Gateway = "192.168.137.1",
  [string]$Interface = "",
  [switch]$Undo
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$group = "SENTINEL-X Serveur"
$tablenet = ($Gateway -replace '\.\d+$', '.0') + "/24"
$dockerRules = Get-NetFirewallRule -Direction Inbound -ErrorAction SilentlyContinue |
  Where-Object { $_.DisplayName -match 'com\.docker\.backend|Docker Desktop' -and $_.Group -ne $group }

if (-not $Interface) {
  $Interface = (Get-NetAdapter -Physical | Where-Object { $_.NdisPhysicalMedium -eq 9 -or $_.InterfaceDescription -match 'Wi-?Fi|Wireless|802\.11' } |
    Select-Object -First 1).Name
}
if (-not $Interface) { throw "Aucune carte Wi-Fi trouvée : précisez -Interface <nom de la carte>" }

if ($Undo) {
  Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
  $dockerRules | Enable-NetFirewallRule
  Set-NetIPInterface -InterfaceAlias $Interface -Dhcp Enabled
  Get-NetIPAddress -InterfaceAlias $Interface -AddressFamily IPv4 -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false
  Get-NetRoute -InterfaceAlias $Interface -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | Remove-NetRoute -Confirm:$false
  Set-DnsClientServerAddress -InterfaceAlias $Interface -ResetServerAddresses
  Write-Host "Configuration réseau et pare-feu d'origine rétablies sur « $Interface »."
  return
}

# --- 1. connexion au point d'accès (WPA2-PSK AES) ---------------------------------------
if ($WifiPassword) {
  $xml = @"
<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>$Ssid</name>
  <SSIDConfig><SSID><name>$Ssid</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>auto</connectionMode>
  <MSM><security>
    <authEncryption><authentication>WPA2PSK</authentication><encryption>AES</encryption><useOneX>false</useOneX></authEncryption>
    <sharedKey><keyType>passPhrase</keyType><protected>false</protected><keyMaterial>$WifiPassword</keyMaterial></sharedKey>
  </security></MSM>
</WLANProfile>
"@
  $tmp = Join-Path $env:TEMP "sentinel-wlan.xml"
  try {
    Set-Content -Path $tmp -Value $xml -Encoding UTF8
    netsh wlan add profile filename="$tmp" interface="$Interface" user=all | Out-Null
  } finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue      # le fichier contient le mot de passe
  }
  netsh wlan connect name="$Ssid" interface="$Interface" | Out-Null
  Start-Sleep 8
}

# --- 2. adresse fixe 192.168.137.10 -------------------------------------------------------------
Set-NetIPInterface -InterfaceAlias $Interface -Dhcp Disabled
Get-NetIPAddress -InterfaceAlias $Interface -AddressFamily IPv4 -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false
Get-NetRoute -InterfaceAlias $Interface -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | Remove-NetRoute -Confirm:$false
New-NetIPAddress -InterfaceAlias $Interface -IPAddress $Ip -PrefixLength 24 -DefaultGateway $Gateway | Out-Null
Set-DnsClientServerAddress -InterfaceAlias $Interface -ServerAddresses $Gateway

# --- 3. pare-feu : refus par défaut, 443/80/8883 depuis le réseau de table uniquement ------------
Set-NetFirewallProfile -Profile Domain, Private, Public -Enabled True -DefaultInboundAction Block -DefaultOutboundAction Allow
Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
$dockerRules | Disable-NetFirewallRule
$backend = Get-ChildItem "$env:ProgramFiles\Docker\Docker\resources\com.docker.backend.exe", "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\com.docker.backend.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
foreach ($rule in @(
    @{ Name = "SENTINEL-X - HTTPS (443)"; Port = 443 },
    @{ Name = "SENTINEL-X - HTTP vers HTTPS (80)"; Port = 80 },
    @{ Name = "SENTINEL-X - MQTTS (8883)"; Port = 8883 })) {
  $ruleArgs = @{ Group = $group; DisplayName = $rule.Name; Direction = "Inbound"; Action = "Allow"; Protocol = "TCP"
             LocalPort = $rule.Port; RemoteAddress = $tablenet; Profile = "Any" }
  if ($backend) { $ruleArgs.Program = $backend.FullName }
  New-NetFirewallRule @ruleArgs | Out-Null
}

# --- 4. Docker ne publie que sur l'IP du réseau de table -----------------------------------------
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
  $content = Get-Content $envFile
  if ($content -match '^BIND_IP=') { $content = $content -replace '^BIND_IP=.*', "BIND_IP=$Ip" } else { $content += "BIND_IP=$Ip" }
  Set-Content -Path $envFile -Value $content -Encoding ASCII
}

Write-Host "Serveur prêt : $Ip sur « $Interface » (passerelle $Gateway)."
Write-Host "Pare-feu : entrée refusée par défaut ; 443/80/8883 autorisés depuis $tablenet uniquement."
Write-Host "Règles Docker « tous ports » désactivées : $($dockerRules.Count). Relancez : docker compose up -d"
