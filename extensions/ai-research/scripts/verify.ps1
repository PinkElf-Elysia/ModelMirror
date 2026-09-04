param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Base,
    [ValidateSet("Quick", "Full")]
    [string]$Mode = "Full",
    [Parameter(Mandatory = $true)]
    [ValidateSet("ExternalPull", "RedistributableBundle")]
    [string]$DistributionMode
)

$ErrorActionPreference = "Stop"
$moduleRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $moduleRoot "..\..")).Path
$composeProject = if ($env:AI_RESEARCH_COMPOSE_PROJECT) {
    $env:AI_RESEARCH_COMPOSE_PROJECT
} else {
    "modelmirror-ai-research"
}
$compose = @("compose", "-p", $composeProject, "-f", "compose.yml", "--profile", "ai-research")
$literatureCompose = @("compose", "-p", $composeProject, "-f", "compose.yml", "--profile", "literature")
$runtime = Join-Path $moduleRoot "runtime"
$diagnostics = Join-Path $runtime ("diagnostics/verify-" + [Guid]::NewGuid().ToString("N"))
$sbom = Join-Path $diagnostics "sbom"
$stackStarted = $false

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed with exit code $LASTEXITCODE" }
}

function Invoke-Python([string[]]$Arguments) {
    Invoke-Checked $pythonFile ($pythonPrefix + $Arguments)
}

function Get-FileSha256([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
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

function Get-ComposeServiceId([string[]]$ComposeArguments, [string]$Service) {
    $containerId = & docker @ComposeArguments ps -q $Service
    if ($LASTEXITCODE -ne 0 -or -not $containerId) {
        throw "running Compose service is missing: $Service"
    }
    return $containerId.Trim()
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

function Resolve-ComparisonBase([string]$RequestedBase) {
    if ([string]::IsNullOrWhiteSpace($RequestedBase) -or $RequestedBase -match "^0+$") {
        throw "comparison base is required and must not be all-zero"
    }
    $resolved = & git -C $repoRoot rev-parse --verify "${RequestedBase}^{commit}"
    if ($LASTEXITCODE -ne 0 -or -not $resolved) {
        throw "comparison base cannot be resolved: $RequestedBase"
    }
    $resolved = $resolved.Trim()
    & git -C $repoRoot merge-base --is-ancestor $resolved HEAD
    if ($LASTEXITCODE -ne 0) {
        throw "comparison base is not an ancestor of HEAD: $resolved"
    }
    return $resolved
}

$comparisonBase = Resolve-ComparisonBase $Base
$trustFiles = @(
    "extensions/ai-research/source-lock.json",
    "extensions/ai-research/module-boundary.json"
)
& git -C $repoRoot diff --quiet --no-ext-diff $comparisonBase HEAD -- @trustFiles
if ($LASTEXITCODE -ne 0) {
    throw "AI Research trust configuration changed in the candidate"
}
& git -C $repoRoot diff --quiet --no-ext-diff --cached HEAD -- @trustFiles
if ($LASTEXITCODE -ne 0) {
    throw "AI Research trust configuration changed in the workspace index"
}
& git -C $repoRoot diff --quiet --no-ext-diff -- @trustFiles
if ($LASTEXITCODE -ne 0) {
    throw "AI Research trust configuration changed in the workspace"
}
$verificationHead = & git -C $repoRoot rev-parse --verify "HEAD^{commit}"
if ($LASTEXITCODE -ne 0) { throw "cannot resolve verification HEAD" }
$verificationHead = $verificationHead.Trim()
if ($Mode -eq "Full") {
    $dirty = & git -C $repoRoot status --porcelain --untracked-files=all
    if ($LASTEXITCODE -ne 0 -or $dirty) {
        throw "Full verification requires a clean worktree; use Quick for local edits"
    }
}
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
New-Item -ItemType Directory -Force -Path $diagnostics, $sbom | Out-Null
$pytestBaseTemp = Join-Path ([System.IO.Path]::GetTempPath()) ("mm-ai-research-pytest-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $pytestBaseTemp | Out-Null
Push-Location $moduleRoot
try {
    $distributionModeValue = if ($DistributionMode -eq "ExternalPull") {
        "external-pull"
    } else {
        "redistributable-bundle"
    }
    $boundaryArgs = @(
        "scripts/validate_boundary.py",
        "--base", $comparisonBase,
        "--distribution-mode", $distributionModeValue
    )
    Invoke-Python $boundaryArgs
    Invoke-Python @(
        "-m", "pytest",
        "tests/control/test_boundary_base.py",
        "tests/control/test_trusted_full_bootstrap.py",
        "tests/control/test_zero_footprint_base.py",
        "-q", "-p", "no:cacheprovider", "--basetemp", $pytestBaseTemp
    )
    Invoke-Checked "docker" ($compose + @("config", "--quiet"))
    Invoke-Checked "docker" ($literatureCompose + @("config", "--quiet"))

    Invoke-Checked "docker" @("build", "--target", "test", "-f", "control/Dockerfile", "-t", "modelmirror-ai-research-control-test:v0.1", ".")
    Invoke-Checked "docker" @("run", "--rm", "modelmirror-ai-research-control-test:v0.1")
    Invoke-Checked "docker" @("build", "--target", "test", "-f", "worker/Dockerfile", "-t", "modelmirror-ai-research-worker-test:v0.1", ".")
    Invoke-Checked "docker" @("run", "--rm", "modelmirror-ai-research-worker-test:v0.1")

    if ($Mode -eq "Quick") { return }

    Invoke-Checked "docker" @("build", "--sbom=true", "--provenance=true", "--target", "runtime", "-f", "control/Dockerfile", "-t", "modelmirror-ai-research-control:v0.1", ".")
    Invoke-Checked "docker" @("build", "--sbom=true", "--provenance=true", "--target", "runtime", "-f", "worker/Dockerfile", "-t", "modelmirror-ai-research-worker:v0.1", ".")
    $controlInventoryPath = Join-Path $sbom "control-runtime-inventory.json"
    $workerInventoryPath = Join-Path $sbom "worker-runtime-inventory.json"
    $uiInventoryPath = Join-Path $sbom "ui-build-inventory.json"
    Extract-ImageFile "modelmirror-ai-research-control:v0.1" "/usr/share/doc/modelmirror-ai-research/runtime-inventory.json" $controlInventoryPath
    Extract-ImageFile "modelmirror-ai-research-control:v0.1" "/usr/share/doc/modelmirror-ai-research/ui-build-inventory.json" $uiInventoryPath
    Extract-ImageFile "modelmirror-ai-research-worker:v0.1" "/usr/share/doc/modelmirror-ai-research/runtime-inventory.json" $workerInventoryPath
    $sourceLock = Get-Content -Raw -Encoding utf8 source-lock.json | ConvertFrom-Json
    $controlInventoryHash = Get-FileSha256 $controlInventoryPath
    $workerInventoryHash = Get-FileSha256 $workerInventoryPath
    $uiInventoryHash = Get-FileSha256 $uiInventoryPath
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
        (Measure-Image "modelmirror-ai-research-control:v0.1" "control"),
        (Measure-Image "modelmirror-ai-research-worker:v0.1" "worker")
    )
    Set-Content -LiteralPath (Join-Path $diagnostics "image-identities.txt") -Value $imageEvidence -Encoding utf8

    $stackStarted = $true
    Invoke-Checked "docker" ($compose + @("up", "-d"))
    $state = Join-Path $diagnostics "acceptance-state.json"
    Invoke-Python @("scripts/acceptance.py", "initial", "--state", $state)
    Invoke-Python @("scripts/acceptance.py", "inspect-view-logs", "--state", $state)
    $viewState = Join-Path $diagnostics "view-degraded-state.json"
    try {
        Invoke-Checked "docker" ($compose + @("stop", "ai-research-inspect-view"))
        Invoke-Python @("scripts/acceptance.py", "view-degraded", "--state", $viewState)
    } finally {
        Invoke-Checked "docker" ($compose + @("start", "ai-research-inspect-view"))
    }
    $outboxState = Join-Path $diagnostics "outbox-state.json"
    Invoke-Python @("scripts/acceptance.py", "outbox-create", "--state", $outboxState)
    try {
        Invoke-Checked "docker" ($compose + @("stop", "ai-research-tracking"))
        Invoke-Python @("scripts/acceptance.py", "required-not-ready", "--state", $outboxState)
        Invoke-Python @("scripts/acceptance.py", "outbox-terminal", "--state", $outboxState)
    } finally {
        Invoke-Checked "docker" ($compose + @("start", "ai-research-tracking"))
    }
    Invoke-Python @("scripts/acceptance.py", "outbox-recovery", "--state", $outboxState)

    $workerRestartState = Join-Path $diagnostics "worker-restart-state.json"
    Invoke-Python @("scripts/acceptance.py", "worker-restart-create", "--state", $workerRestartState)
    Invoke-Checked "docker" ($compose + @("restart", "ai-research-worker"))
    Invoke-Python @("scripts/acceptance.py", "worker-restart-recovery", "--state", $workerRestartState)
    for ($round = 1; $round -le 2; $round++) {
        Invoke-Checked "docker" ($compose + @("restart", "ai-research-control", "ai-research-tracking"))
        Invoke-Python @("scripts/acceptance.py", "recovery", "--state", $state)
    }

    $literatureState = $null
    if ($env:AI_RESEARCH_LIVE_ACCEPTANCE -eq "1") {
        Invoke-Checked "docker" ($literatureCompose + @("up", "-d", "ai-research-model-relay", "ai-research-ldr"))
        $literatureState = Join-Path $diagnostics "literature-acceptance-state.json"
        Invoke-Python @("scripts/literature_acceptance.py", "initial", "--state", $literatureState)
        for ($round = 1; $round -le 2; $round++) {
            Invoke-Checked "docker" ($literatureCompose + @("restart", "ai-research-control", "ai-research-ldr"))
            Invoke-Python @("scripts/literature_acceptance.py", "recovery", "--state", $literatureState)
        }
    } else {
        Write-Warning "Live model/OpenAlex/Zotero journey was not run; V0.1 real acceptance remains open"
    }

    $runIds = @((Get-Content -Raw -Encoding utf8 $state | ConvertFrom-Json).runs)
    $runIds += (Get-Content -Raw -Encoding utf8 $viewState | ConvertFrom-Json).runId
    $runIds += (Get-Content -Raw -Encoding utf8 $outboxState | ConvertFrom-Json).runId
    $runIds += (Get-Content -Raw -Encoding utf8 $workerRestartState | ConvertFrom-Json).runId
    $auditArgs = $compose + @("exec", "-T", "ai-research-control", "python", "-m", "ai_research_control.audit_runtime")
    foreach ($runId in $runIds) { $auditArgs += @("--run-id", $runId) }
    $runtimeAuditPath = Join-Path $diagnostics "runtime-audit.json"
    $auditOutput = & docker @auditArgs
    if ($LASTEXITCODE -ne 0) { throw "runtime evidence audit failed" }
    [System.IO.File]::WriteAllText(
        $runtimeAuditPath,
        (($auditOutput -join "`n").Trim() + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )

    $oversizedAttack = "import json,os,socket; s=socket.socket(socket.AF_UNIX); s.settimeout(5); s.connect(os.environ['AI_RESEARCH_WORKER_SOCKET']); s.sendall(b'x'*70000+b'\n'); value=json.loads(s.makefile('rb').readline()); s.close(); assert value['ok'] is False"
    Invoke-Checked "docker" ($compose + @("exec", "-T", "ai-research-control", "python", "-c", $oversizedAttack))

    $networkAttack = @'
import socket
import urllib.request

unexpected = []
checks = [
    ('dns', lambda: socket.getaddrinfo('example.com', 443)),
    ('tcp', lambda: socket.create_connection(('1.1.1.1', 443), timeout=1)),
    ('http', lambda: urllib.request.urlopen('http://example.com', timeout=1)),
    ('host', lambda: socket.getaddrinfo('host.docker.internal', 8000)),
]
for name, check in checks:
    try:
        check()
    except OSError:
        continue
    unexpected.append(name)
if unexpected:
    raise SystemExit('network unexpectedly available: ' + ','.join(unexpected))
'@
    & docker @compose exec -T ai-research-worker python -c $networkAttack
    if ($LASTEXITCODE -ne 0) { throw "worker network isolation attack failed" }
    & docker @compose exec -T ai-research-control python -c $networkAttack
    if ($LASTEXITCODE -ne 0) { throw "control network isolation attack failed" }

    $environmentServices = @("ai-research-control", "ai-research-console-gateway", "ai-research-tracking", "ai-research-worker", "ai-research-inspect-view")
    if ($env:AI_RESEARCH_LIVE_ACCEPTANCE -eq "1") {
        $environmentServices += @("ai-research-model-relay", "ai-research-ldr")
    }
    $containerEnv = @(
        foreach ($service in $environmentServices) {
            $containerId = Get-ComposeServiceId $literatureCompose $service
            & docker inspect $containerId --format "{{json .Config.Env}}"
            if ($LASTEXITCODE -ne 0) { throw "failed to inspect Compose service: $service" }
        }
    ) -join "`n"
    if ($containerEnv -match "OPENROUTER_API_KEY|LLM_GATEWAY_KEY|DIFY_API_KEY|PROVIDER.*(KEY|TOKEN|SECRET)|sk-(or-v1-)?[A-Za-z0-9_-]{32,}|gh[pousr]_[A-Za-z0-9]{30,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY") {
        throw "provider credential names were exposed to module containers"
    }
    $securityAttacksPath = Join-Path $diagnostics "security-attacks.json"
    $securityEvidence = [ordered]@{
        schemaVersion = 1
        status = "passed"
        checks = @(
            "oversized_worker_protocol_rejected",
            "worker_network_isolated",
            "control_public_network_isolated",
            "module_container_credentials_absent"
        )
    } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText(
        $securityAttacksPath,
        ($securityEvidence + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )

    $sourceLock = Get-Content -Raw -LiteralPath (Join-Path $moduleRoot "source-lock.json") | ConvertFrom-Json
    $clientSourceProof = Join-Path $runtime ("client-source-proof-" + [Guid]::NewGuid().ToString("N"))
    $clientBaselineProof = Join-Path $runtime ("client-baseline-proof-" + [Guid]::NewGuid().ToString("N"))
    $clientCurrentProof = Join-Path $runtime ("client-current-proof-" + [Guid]::NewGuid().ToString("N"))
    $clientSourceContext = Join-Path $runtime ("client-source-context-" + [Guid]::NewGuid().ToString("N"))
    $clientBaselineContext = Join-Path $runtime ("client-baseline-context-" + [Guid]::NewGuid().ToString("N"))
    $clientCurrentContext = Join-Path $runtime ("client-current-context-" + [Guid]::NewGuid().ToString("N"))
    $clientPaths = @(
        $clientSourceProof,
        $clientBaselineProof,
        $clientCurrentProof,
        $clientSourceContext,
        $clientBaselineContext,
        $clientCurrentContext
    )
    New-Item -ItemType Directory -Path $clientPaths | Out-Null
    try {
        Build-ClientProof `
            $sourceLock.modelMirrorBaseCommit `
            $clientSourceContext `
            "modelmirror-ai-research-client-proof:v0.1-source"
        Build-ClientProof `
            $comparisonBase `
            $clientBaselineContext `
            "modelmirror-ai-research-client-proof:v0.1-baseline"
        Build-ClientProof `
            "HEAD" `
            $clientCurrentContext `
            "modelmirror-ai-research-client-proof:v0.1"
        Extract-ImageFile `
            "modelmirror-ai-research-client-proof:v0.1-source" `
            "/proof/dist/." `
            $clientSourceProof
        Extract-ImageFile `
            "modelmirror-ai-research-client-proof:v0.1-baseline" `
            "/proof/dist/." `
            $clientBaselineProof
        Extract-ImageFile `
            "modelmirror-ai-research-client-proof:v0.1" `
            "/proof/dist/." `
            $clientCurrentProof
        $zeroFootprintPath = Join-Path $diagnostics "zero-footprint.json"
        $zeroFootprintArgs = @(
            "scripts/zero_footprint.py",
            "--base", $comparisonBase,
            "--source-client-dist", $clientSourceProof,
            "--baseline-client-dist", $clientBaselineProof,
            "--client-dist", $clientCurrentProof
        )
        $zeroFootprintOutput = & $pythonFile @($pythonPrefix + $zeroFootprintArgs)
        if ($LASTEXITCODE -ne 0) { throw "zero-footprint validation failed" }
        [System.IO.File]::WriteAllText(
            $zeroFootprintPath,
            (($zeroFootprintOutput -join "`n").Trim() + "`n"),
            [System.Text.UTF8Encoding]::new($false)
        )
        $manifestArgs = @(
            "scripts/acceptance_manifest.py",
            "--base", $comparisonBase,
            "--expected-head", $verificationHead,
            "--evidence-root", $diagnostics,
            "--distribution-mode", $distributionModeValue,
            "--output", (Join-Path $diagnostics "full-acceptance-manifest.json")
        )
        $jsonEvidence = @(
            $state,
            $viewState,
            $outboxState,
            $workerRestartState,
            $runtimeAuditPath,
            $securityAttacksPath,
            $zeroFootprintPath
        )
        if ($literatureState) { $jsonEvidence += $literatureState }
        foreach ($path in $jsonEvidence) {
            $manifestArgs += @("--json-evidence", $path)
        }
        foreach ($path in @(
            (Join-Path $diagnostics "image-identities.txt"),
            $controlInventoryPath,
            $workerInventoryPath,
            $uiInventoryPath
        )) {
            $manifestArgs += @("--hashed-evidence", $path)
        }
    } finally {
        foreach ($clientPath in $clientPaths) {
            if (Test-Path -LiteralPath $clientPath) {
                Remove-Item -LiteralPath $clientPath -Recurse -Force
            }
        }
    }
    Invoke-Checked "docker" ($literatureCompose + @("down"))
    $stackStarted = $false
    Invoke-Python $manifestArgs
} finally {
    if ($stackStarted) { & docker @literatureCompose down }
    Pop-Location
    if (Test-Path -LiteralPath $pytestBaseTemp) {
        Remove-Item -LiteralPath $pytestBaseTemp -Recurse -Force
    }
}
