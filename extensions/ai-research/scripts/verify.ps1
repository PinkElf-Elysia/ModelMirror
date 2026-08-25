param(
    [string]$Base = "origin/main",
    [ValidateSet("Quick", "Full")]
    [string]$Mode = "Full"
)

$ErrorActionPreference = "Stop"
$moduleRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $moduleRoot "..\..")).Path
$pythonFile = $env:AI_RESEARCH_PYTHON
$pythonPrefix = @()
if (-not $pythonFile) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $pythonFile = $pythonCommand.Source
    } else {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if (-not $launcher) { throw "Set AI_RESEARCH_PYTHON to a Python 3.12.13 executable" }
        $pythonFile = $launcher.Source
        $pythonPrefix = @("-3.12")
    }
}
$compose = @("compose", "-f", "compose.yml", "--profile", "ai-research")
$runtime = Join-Path $moduleRoot "runtime"
$diagnostics = Join-Path $runtime "diagnostics"
$sbom = Join-Path $runtime "sbom"
New-Item -ItemType Directory -Force -Path $diagnostics, $sbom | Out-Null

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed with exit code $LASTEXITCODE" }
}

function Invoke-Python([string[]]$Arguments) {
    Invoke-Checked $pythonFile ($pythonPrefix + $Arguments)
}

function Measure-Image([string]$Image, [string]$Slug) {
    $tarPath = Join-Path $diagnostics "$Slug-image.tar"
    $gzipPath = "$tarPath.gz"
    Invoke-Checked "docker" @("save", "-o", $tarPath, $Image)
    $input = [System.IO.File]::OpenRead($tarPath)
    $output = [System.IO.File]::Create($gzipPath)
    try {
        $gzip = [System.IO.Compression.GZipStream]::new(
            $output, [System.IO.Compression.CompressionLevel]::Optimal
        )
        try { $input.CopyTo($gzip) } finally { $gzip.Dispose() }
    } finally {
        $input.Dispose()
        $output.Dispose()
    }
    $identity = & docker image inspect $Image --format "{{.Id}}|{{.Size}}"
    if ($LASTEXITCODE -ne 0) { throw "failed to inspect $Image" }
    $archiveBytes = (Get-Item -LiteralPath $tarPath).Length
    $compressedBytes = (Get-Item -LiteralPath $gzipPath).Length
    Remove-Item -LiteralPath $tarPath, $gzipPath
    return "$identity|archiveBytes=$archiveBytes|gzipBytes=$compressedBytes"
}

function Extract-ImageFile([string]$Image, [string]$ContainerPath, [string]$Destination) {
    $containerId = & docker create $Image
    if ($LASTEXITCODE -ne 0 -or -not $containerId) { throw "failed to create extractor for $Image" }
    try {
        Invoke-Checked "docker" @("cp", "${containerId}:$ContainerPath", $Destination)
    } finally {
        & docker rm $containerId | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "failed to remove extractor $containerId" }
    }
}

function Build-ClientProof([string]$GitRef, [string]$Context, [string]$Image) {
    $archive = Join-Path $Context "client.tar"
    Invoke-Checked "git" @(
        "-C", $repoRoot,
        "archive", "--format=tar", "--output=$archive", $GitRef, "client"
    )
    Invoke-Checked "tar" @("-xf", $archive, "-C", $Context)
    Remove-Item -LiteralPath $archive
    Invoke-Checked "docker" @(
        "build",
        "-f", (Join-Path $moduleRoot "scripts/client-proof.Dockerfile"),
        "-t", $Image,
        $Context
    )
}

Push-Location $moduleRoot
try {
    Invoke-Python @("scripts/validate_boundary.py", "--base", $Base)
    Invoke-Checked "docker" ($compose + @("config", "--quiet"))

    Invoke-Checked "docker" @("build", "--target", "test", "-f", "control/Dockerfile", "-t", "modelmirror-ai-research-control-test:ar1", ".")
    Invoke-Checked "docker" @("run", "--rm", "modelmirror-ai-research-control-test:ar1")
    Invoke-Checked "docker" @("build", "--target", "test", "-f", "worker/Dockerfile", "-t", "modelmirror-ai-research-worker-test:ar1", ".")
    Invoke-Checked "docker" @("run", "--rm", "modelmirror-ai-research-worker-test:ar1")

    if ($Mode -eq "Quick") { return }

    Invoke-Checked "docker" @("build", "--sbom=true", "--provenance=true", "--target", "runtime", "-f", "control/Dockerfile", "-t", "modelmirror-ai-research-control:ar1", ".")
    Invoke-Checked "docker" @("build", "--sbom=true", "--provenance=true", "--target", "runtime", "-f", "worker/Dockerfile", "-t", "modelmirror-ai-research-worker:ar1", ".")
    $controlInventoryPath = Join-Path $sbom "control-runtime-inventory.json"
    $workerInventoryPath = Join-Path $sbom "worker-runtime-inventory.json"
    $uiInventoryPath = Join-Path $sbom "ui-build-inventory.json"
    Extract-ImageFile "modelmirror-ai-research-control:ar1" "/usr/share/doc/modelmirror-ai-research/runtime-inventory.json" $controlInventoryPath
    Extract-ImageFile "modelmirror-ai-research-control:ar1" "/usr/share/doc/modelmirror-ai-research/ui-build-inventory.json" $uiInventoryPath
    Extract-ImageFile "modelmirror-ai-research-worker:ar1" "/usr/share/doc/modelmirror-ai-research/runtime-inventory.json" $workerInventoryPath
    $sourceLock = Get-Content -Raw -Encoding utf8 source-lock.json | ConvertFrom-Json
    $controlInventoryHash = (Get-FileHash -Algorithm SHA256 $controlInventoryPath).Hash.ToLowerInvariant()
    $workerInventoryHash = (Get-FileHash -Algorithm SHA256 $workerInventoryPath).Hash.ToLowerInvariant()
    $uiInventoryHash = (Get-FileHash -Algorithm SHA256 $uiInventoryPath).Hash.ToLowerInvariant()
    if ($controlInventoryHash -ne $sourceLock.licenseAudit.control.inventorySha256) {
        throw "control runtime inventory hash disagrees with source-lock"
    }
    if ($workerInventoryHash -ne $sourceLock.licenseAudit.worker.inventorySha256) {
        throw "worker runtime inventory hash disagrees with source-lock"
    }
    if ($uiInventoryHash -ne $sourceLock.licenseAudit.ui.inventorySha256) {
        throw "UI build inventory hash disagrees with source-lock"
    }
    $imageEvidence = @(
        (Measure-Image "modelmirror-ai-research-control:ar1" "control"),
        (Measure-Image "modelmirror-ai-research-worker:ar1" "worker")
    )
    Set-Content -LiteralPath (Join-Path $diagnostics "image-identities.txt") -Value $imageEvidence -Encoding utf8

    Invoke-Checked "docker" ($compose + @("up", "-d"))
    $state = Join-Path $runtime "acceptance-state.json"
    Invoke-Python @("scripts/acceptance.py", "initial", "--state", $state)
    Invoke-Python @("scripts/acceptance.py", "inspect-view-logs", "--state", $state)
    $viewState = Join-Path $runtime "view-degraded-state.json"
    try {
        Invoke-Checked "docker" ($compose + @("stop", "ai-research-inspect-view"))
        Invoke-Python @("scripts/acceptance.py", "view-degraded", "--state", $viewState)
    } finally {
        Invoke-Checked "docker" ($compose + @("start", "ai-research-inspect-view"))
    }
    $outboxState = Join-Path $runtime "outbox-state.json"
    Invoke-Python @("scripts/acceptance.py", "outbox-create", "--state", $outboxState)
    try {
        Invoke-Checked "docker" ($compose + @("stop", "ai-research-tracking"))
        Invoke-Python @("scripts/acceptance.py", "required-not-ready", "--state", $outboxState)
        Invoke-Python @("scripts/acceptance.py", "outbox-terminal", "--state", $outboxState)
    } finally {
        Invoke-Checked "docker" ($compose + @("start", "ai-research-tracking"))
    }
    Invoke-Python @("scripts/acceptance.py", "outbox-recovery", "--state", $outboxState)

    $workerRestartState = Join-Path $runtime "worker-restart-state.json"
    Invoke-Python @("scripts/acceptance.py", "worker-restart-create", "--state", $workerRestartState)
    Invoke-Checked "docker" ($compose + @("restart", "ai-research-worker"))
    Invoke-Python @("scripts/acceptance.py", "worker-restart-recovery", "--state", $workerRestartState)
    for ($round = 1; $round -le 2; $round++) {
        Invoke-Checked "docker" ($compose + @("restart", "ai-research-control", "ai-research-tracking"))
        Invoke-Python @("scripts/acceptance.py", "recovery", "--state", $state)
    }

    $runIds = @((Get-Content -Raw -Encoding utf8 $state | ConvertFrom-Json).runs)
    $runIds += (Get-Content -Raw -Encoding utf8 $outboxState | ConvertFrom-Json).runId
    $runIds += (Get-Content -Raw -Encoding utf8 $workerRestartState | ConvertFrom-Json).runId
    $auditArgs = $compose + @("exec", "-T", "ai-research-control", "python", "-m", "ai_research_control.audit_runtime")
    foreach ($runId in $runIds) { $auditArgs += @("--run-id", $runId) }
    Invoke-Checked "docker" $auditArgs

    $oversizedAttack = "import json,os,socket; s=socket.socket(socket.AF_UNIX); s.settimeout(5); s.connect(os.environ['AI_RESEARCH_WORKER_SOCKET']); s.sendall(b'x'*70000+b'\n'); value=json.loads(s.makefile('rb').readline()); s.close(); assert value['ok'] is False"
    Invoke-Checked "docker" ($compose + @("exec", "-T", "ai-research-control", "python", "-c", $oversizedAttack))

    $networkAttack = @'
import socket
import urllib.request

unexpected = []
checks = [
    ("dns", lambda: socket.getaddrinfo("example.com", 443)),
    ("tcp", lambda: socket.create_connection(("1.1.1.1", 443), timeout=1)),
    ("http", lambda: urllib.request.urlopen("http://example.com", timeout=1)),
]
for name, check in checks:
    try:
        check()
    except OSError:
        continue
    unexpected.append(name)
if unexpected:
    raise SystemExit("network unexpectedly available: " + ",".join(unexpected))
'@
    & docker @compose exec -T ai-research-worker python -c $networkAttack
    if ($LASTEXITCODE -ne 0) { throw "worker network isolation attack failed" }

    $containerEnv = @(
        (& docker inspect modelmirror-ai-research-ai-research-control-1 --format "{{json .Config.Env}}"),
        (& docker inspect modelmirror-ai-research-ai-research-tracking-1 --format "{{json .Config.Env}}"),
        (& docker inspect modelmirror-ai-research-ai-research-worker-1 --format "{{json .Config.Env}}"),
        (& docker inspect modelmirror-ai-research-ai-research-inspect-view-1 --format "{{json .Config.Env}}")
    ) -join "`n"
    if ($containerEnv -match "OPENROUTER_API_KEY|LLM_GATEWAY_KEY|DIFY_API_KEY|PROVIDER.*(KEY|TOKEN|SECRET)|sk-(or-v1-)?[A-Za-z0-9_-]{32,}|gh[pousr]_[A-Za-z0-9]{30,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY") {
        throw "provider credential names were exposed to module containers"
    }

    $sourceLock = Get-Content -Raw -LiteralPath (Join-Path $moduleRoot "source-lock.json") | ConvertFrom-Json
    $clientBaselineProof = Join-Path $runtime ("client-baseline-proof-" + [Guid]::NewGuid().ToString("N"))
    $clientCurrentProof = Join-Path $runtime ("client-current-proof-" + [Guid]::NewGuid().ToString("N"))
    $clientBaselineContext = Join-Path $runtime ("client-baseline-context-" + [Guid]::NewGuid().ToString("N"))
    $clientCurrentContext = Join-Path $runtime ("client-current-context-" + [Guid]::NewGuid().ToString("N"))
    $clientPaths = @(
        $clientBaselineProof,
        $clientCurrentProof,
        $clientBaselineContext,
        $clientCurrentContext
    )
    New-Item -ItemType Directory -Path $clientPaths | Out-Null
    try {
        Build-ClientProof `
            $sourceLock.modelMirrorBaseCommit `
            $clientBaselineContext `
            "modelmirror-ai-research-client-proof:ar1-baseline"
        Build-ClientProof `
            "HEAD" `
            $clientCurrentContext `
            "modelmirror-ai-research-client-proof:ar1"
        Extract-ImageFile `
            "modelmirror-ai-research-client-proof:ar1-baseline" `
            "/proof/dist/." `
            $clientBaselineProof
        Extract-ImageFile `
            "modelmirror-ai-research-client-proof:ar1" `
            "/proof/dist/." `
            $clientCurrentProof
        Invoke-Python @(
            "scripts/zero_footprint.py",
            "--baseline-client-dist", $clientBaselineProof,
            "--client-dist", $clientCurrentProof
        )
    } finally {
        foreach ($clientPath in $clientPaths) {
            if (Test-Path -LiteralPath $clientPath) {
                Remove-Item -LiteralPath $clientPath -Recurse -Force
            }
        }
    }
} finally {
    & docker @compose down
    Pop-Location
}
