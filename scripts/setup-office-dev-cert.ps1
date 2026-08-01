[CmdletBinding()]
param(
    [switch]$SkipTrust
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$certDir = Join-Path $repoRoot "server\office_host\certs"
New-Item -ItemType Directory -Path $certDir -Force | Out-Null

$git = Get-Command git -ErrorAction SilentlyContinue
$gitRoot = if ($git) {
    Split-Path -Parent (Split-Path -Parent $git.Source)
} else {
    $null
}
$gitOpenSsl = if ($gitRoot) {
    Join-Path $gitRoot "usr\bin\openssl.exe"
} else {
    $null
}
$opensslPath = if ($gitOpenSsl -and (Test-Path -LiteralPath $gitOpenSsl)) {
    $gitOpenSsl
} else {
    (Get-Command openssl -ErrorAction SilentlyContinue).Source
}
if (-not $opensslPath) {
    throw "OpenSSL is required to create the Office localhost certificate. Install Git for Windows or OpenSSL and retry."
}
$configCandidates = @(
    if ($gitRoot) { Join-Path $gitRoot "usr\ssl\openssl.cnf" }
    if ($gitRoot) { Join-Path $gitRoot "mingw64\etc\ssl\openssl.cnf" }
    Join-Path (Split-Path -Parent $opensslPath) "..\ssl\openssl.cnf"
)
$opensslConfig = $configCandidates |
    Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
    Select-Object -First 1
if (-not $opensslConfig) {
    throw "OpenSSL configuration was not found. Install Git for Windows or set up OpenSSL and retry."
}

$certificatePath = Join-Path $certDir "localhost.crt"
$privateKeyPath = Join-Path $certDir "localhost.key"
& $opensslPath req `
    -config $opensslConfig `
    -x509 `
    -nodes `
    -newkey rsa:2048 `
    -sha256 `
    -days 730 `
    -subj "/CN=localhost" `
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1" `
    -addext "basicConstraints=critical,CA:FALSE" `
    -addext "keyUsage=critical,digitalSignature,keyEncipherment" `
    -addext "extendedKeyUsage=serverAuth" `
    -keyout $privateKeyPath `
    -out $certificatePath
if ($LASTEXITCODE -ne 0) {
    throw "OpenSSL failed to create the Office localhost certificate."
}

if (-not $SkipTrust) {
    $imported = Import-Certificate `
        -FilePath $certificatePath `
        -CertStoreLocation "Cert:\CurrentUser\Root"
    if (-not $imported) {
        throw "Failed to trust the Office localhost certificate for the current user."
    }
}

Write-Host "Office development certificate created in $certDir"
Write-Host "Start with: docker compose --profile office up -d --build --force-recreate"
