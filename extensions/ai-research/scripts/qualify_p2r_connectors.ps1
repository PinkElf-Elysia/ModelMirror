param(
    [Parameter(Mandatory = $true)]
    [string]$SkillRoot,
    [Parameter(Mandatory = $true)]
    [string]$OutputParent
)

$ErrorActionPreference = "Stop"

function Assert-LocalPathInput {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($Path.StartsWith("\\") -or $Path.StartsWith("//") -or $Path -match '^[^:]+::[\\/]{2}') {
        throw "$Label must not be a UNC or provider-qualified network path"
    }
}

function New-IsolatedWheelRoot {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$RequirementsLock,
        [Parameter(Mandatory = $true)][string]$ExpectedRequirementsLockSha256
    )
    $lockDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $RequirementsLock).Hash.ToLowerInvariant()
    if ($lockDigest -ne $ExpectedRequirementsLockSha256) {
        throw "P2R connector requirements lock changed after license validation"
    }
    $lockText = Get-Content -Raw -Encoding utf8 -LiteralPath $RequirementsLock
    $expected = @{}
    foreach ($match in [regex]::Matches($lockText, '--hash=sha256:([0-9a-f]{64})')) {
        $expected[$match.Groups[1].Value] = $false
    }
    if ($expected.Count -ne 17) {
        throw "P2R connector lock must contain exactly 17 unique wheel hashes"
    }
    $targetRoot = Join-Path ([IO.Path]::GetTempPath()) ("modelmirror-p2r-wheelset-" + [Guid]::NewGuid().ToString("N"))
    [IO.Directory]::CreateDirectory($targetRoot) | Out-Null
    try {
        foreach ($wheel in Get-ChildItem -LiteralPath $SourceRoot -File -Filter "*.whl") {
            if (($wheel.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "P2R connector wheelhouse must not contain reparse points"
            }
            $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $wheel.FullName).Hash.ToLowerInvariant()
            if (-not $expected.ContainsKey($digest)) {
                continue
            }
            if ($expected[$digest]) {
                throw "P2R connector wheelhouse contains a duplicate locked digest"
            }
            $destination = Join-Path $targetRoot $wheel.Name
            Copy-Item -LiteralPath $wheel.FullName -Destination $destination
            $copiedDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash.ToLowerInvariant()
            if ($copiedDigest -ne $digest) {
                throw "P2R connector wheel changed while constructing the isolated view"
            }
            $expected[$digest] = $true
        }
        $missing = @($expected.GetEnumerator() | Where-Object { -not $_.Value })
        if ($missing.Count -ne 0) {
            throw "P2R connector wheelhouse is missing one or more locked wheels"
        }
        $finalLockDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $RequirementsLock).Hash.ToLowerInvariant()
        if ($finalLockDigest -ne $ExpectedRequirementsLockSha256) {
            throw "P2R connector requirements lock changed while constructing the isolated view"
        }
        $snapshotLock = Join-Path $targetRoot "requirements.lock"
        Copy-Item -LiteralPath $RequirementsLock -Destination $snapshotLock
        $snapshotLockDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $snapshotLock).Hash.ToLowerInvariant()
        if ($snapshotLockDigest -ne $ExpectedRequirementsLockSha256) {
            throw "P2R connector requirements lock changed while creating its snapshot"
        }
        return (Resolve-Path -LiteralPath $targetRoot).Path
    } catch {
        Get-ChildItem -LiteralPath $targetRoot -File -Force -ErrorAction SilentlyContinue | Remove-Item -Force
        Remove-Item -LiteralPath $targetRoot -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Remove-IsolatedWheelRoot {
    param([Parameter(Mandatory = $true)][string]$Path)
    Get-ChildItem -LiteralPath $Path -File -Force -ErrorAction SilentlyContinue | Remove-Item -Force
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}

$moduleRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workerPackageRoot = (Resolve-Path (Join-Path $moduleRoot "worker\ai_research_worker")).Path
$image = "python@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461"
$wheelRoot = Join-Path $moduleRoot "runtime\p2r-connector-lock"
$lockPath = Join-Path $moduleRoot "worker\p2r-connectors-linux-x86_64.requirements.lock"
$licenseGatePath = Join-Path $moduleRoot "scripts\validate_boundary.py"

foreach ($path in @($workerPackageRoot, $wheelRoot, $lockPath, $licenseGatePath)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required P2R qualification input is missing: $path"
    }
    $item = Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "P2R qualification inputs must not be reparse points: $path"
    }
}
Assert-LocalPathInput -Path $SkillRoot -Label "SkillRoot"
Assert-LocalPathInput -Path $OutputParent -Label "OutputParent"

$pythonFile = $env:AI_RESEARCH_PYTHON
$pythonPrefix = @()
if (-not $pythonFile) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $pythonFile = $pythonCommand.Source
    } else {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if (-not $launcher) {
            throw "Set AI_RESEARCH_PYTHON to a Python 3.12.13 executable"
        }
        $pythonFile = $launcher.Source
        $pythonPrefix = @("-3.12")
    }
}
Assert-LocalPathInput -Path $pythonFile -Label "AI_RESEARCH_PYTHON"
$pythonFile = (Resolve-Path -LiteralPath $pythonFile).Path
$pythonItem = Get-Item -LiteralPath $pythonFile -Force
if ($pythonItem.PSIsContainer -or ($pythonItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "AI_RESEARCH_PYTHON must be a local regular file"
}
$pythonVersionOutput = @(& $pythonFile @pythonPrefix -c "import platform; print(platform.python_version())" 2>&1)
if ($LASTEXITCODE -ne 0 -or $pythonVersionOutput.Count -ne 1 -or $pythonVersionOutput[0].ToString().Trim() -ne "3.12.13") {
    throw "AI_RESEARCH_PYTHON must report exactly Python 3.12.13"
}
$validatedLockDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $lockPath).Hash.ToLowerInvariant()
$licenseGateOutput = @(& $pythonFile @pythonPrefix $licenseGatePath --p2r-license-only --p2r-wheel-root $wheelRoot 2>&1)
$licenseGateExit = $LASTEXITCODE
$licenseGateOutput | ForEach-Object { Write-Host $_.ToString() }
if ($licenseGateExit -ne 0) {
    throw "P2R connector license validation failed before qualification"
}
if (-not @($licenseGateOutput | Where-Object { $_.ToString().StartsWith("P2R license validation passed:") })) {
    throw "P2R connector license validation did not emit its success marker"
}
$currentLockDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $lockPath).Hash.ToLowerInvariant()
if ($currentLockDigest -ne $validatedLockDigest) {
    throw "P2R connector requirements lock changed during license validation"
}
$skillRootResolved = (Resolve-Path -LiteralPath $SkillRoot).Path
$skillRootItem = Get-Item -LiteralPath $skillRootResolved -Force
if (-not $skillRootItem.PSIsContainer -or ($skillRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "SkillRoot must resolve to a local regular directory"
}

if (Test-Path -LiteralPath $OutputParent) {
    $outputItem = Get-Item -LiteralPath $OutputParent -Force
    if (-not $outputItem.PSIsContainer -or ($outputItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Existing OutputParent must be a safe directory"
    }
    $existingNames = @(Get-ChildItem -LiteralPath $OutputParent -Force | Select-Object -ExpandProperty Name)
    if ($existingNames.Count -ne 1 -or $existingNames[0] -ne "p2r-input-receipt.json") {
        throw "Existing OutputParent must be a fresh P2R run containing only p2r-input-receipt.json"
    }
} else {
    New-Item -ItemType Directory -Path $OutputParent | Out-Null
}
$outputResolved = (Resolve-Path -LiteralPath $OutputParent).Path
$containerName = "modelmirror-p2r-connectors-" + [Guid]::NewGuid().ToString("N")

$preflightArguments = @(
    "run", "--rm",
    "--network", "none",
    "--read-only",
    "--user", "65532:65532",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--pids-limit", "32",
    "--memory", "256m",
    "--cpus", "1",
    "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=32m",
    "--workdir", "/tmp",
    "--mount", "type=bind,source=$workerPackageRoot,target=/module/ai_research_worker,readonly",
    "--mount", "type=bind,source=$skillRootResolved,target=/skill,readonly",
    "--mount", "type=bind,source=$lockPath,target=/lock/requirements.lock,readonly",
    "-e", "PYTHONDONTWRITEBYTECODE=1",
    "-e", "PYTHONPATH=/module",
    "--entrypoint", "python",
    $image,
    "-m", "ai_research_worker.p2r_connectors",
    "--requirements-lock", "/lock/requirements.lock",
    "--skill-root", "/skill",
    "--verify-only"
)
& docker @preflightArguments
if ($LASTEXITCODE -ne 0) {
    throw "P2R locked ResearchStudio source preflight failed"
}

$isolatedWheelRoot = New-IsolatedWheelRoot `
    -SourceRoot $wheelRoot `
    -RequirementsLock $lockPath `
    -ExpectedRequirementsLockSha256 $validatedLockDigest
$isolatedLockPath = Join-Path $isolatedWheelRoot "requirements.lock"
try {
$openReviewUser = Read-Host "OpenReview username or email"
$openReviewSecure = Read-Host "OpenReview password (input is masked)" -AsSecureString
$openReviewBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($openReviewSecure)
$dockerExit = 1
try {
    $openReviewPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($openReviewBstr)
    if ([string]::IsNullOrWhiteSpace($openReviewUser) -or [string]::IsNullOrEmpty($openReviewPassword)) {
        throw "OpenReview username and password are required"
    }
    $env:OPENREVIEW_USER = $openReviewUser
    $env:OPENREVIEW_PASS = $openReviewPassword
    $dockerArguments = @(
        "run", "--rm",
        "--name", $containerName,
        "--read-only",
        "--user", "65532:65532",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",
        "--memory", "512m",
        "--cpus", "1",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=128m",
        "--tmpfs", "/runtime:rw,exec,nosuid,nodev,size=256m",
        "--workdir", "/runtime",
        "--mount", "type=bind,source=$workerPackageRoot,target=/module/ai_research_worker,readonly",
        "--mount", "type=bind,source=$skillRootResolved,target=/skill,readonly",
        "--mount", "type=bind,source=$outputResolved,target=/out",
        "--mount", "type=bind,source=$isolatedWheelRoot,target=/wheels,readonly",
        "--mount", "type=bind,source=$isolatedLockPath,target=/lock/requirements.lock,readonly",
        "-e", "OPENREVIEW_USER",
        "-e", "OPENREVIEW_PASS",
        "-e", "PIP_NO_CACHE_DIR=1",
        "--entrypoint", "sh",
        $image,
        "-c",
        "python -m pip install --disable-pip-version-check --no-index --find-links /wheels --require-hashes --target /runtime/site -r /lock/requirements.lock >/runtime/pip.log && PYTHONPATH=/module:/runtime/site python -m ai_research_worker.p2r_connectors --requirements-lock /lock/requirements.lock --skill-root /skill --output-parent /out"
    )
    & docker @dockerArguments
    $dockerExit = $LASTEXITCODE
} finally {
    Remove-Item Env:OPENREVIEW_USER -ErrorAction SilentlyContinue
    Remove-Item Env:OPENREVIEW_PASS -ErrorAction SilentlyContinue
    if ($openReviewBstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($openReviewBstr)
    }
    $openReviewPassword = $null
    $openReviewSecure = $null
    $openReviewUser = $null
}

$receiptPath = Join-Path $outputResolved "connector-qualification\connector-receipt.json"
if (Test-Path -LiteralPath $receiptPath) {
    $receipt = Get-Content -Raw -Encoding utf8 -LiteralPath $receiptPath | ConvertFrom-Json
    $facts = foreach ($property in $receipt.connectors.PSObject.Properties) {
        [pscustomobject]@{
            Connector = $property.Name
            Status = $property.Value.status
            HitCount = $property.Value.hitCount
            AuthMode = $property.Value.authMode
            ErrorType = $property.Value.error.type
            ErrorCategory = $property.Value.error.category
            ErrorStage = $property.Value.error.stage
            UpstreamType = $property.Value.error.upstreamType
            UpstreamError = $property.Value.error.upstreamErrorName
            HttpStatus = $property.Value.error.httpStatus
            ProbeAttempts = @($property.Value.probeAttempts).Count
        }
    }
    $facts | Format-Table -AutoSize
    Write-Host "Receipt: $receiptPath"
    Write-Host ("Receipt SHA-256: " + (Get-FileHash -Algorithm SHA256 -LiteralPath $receiptPath).Hash.ToLowerInvariant())
}

if ($dockerExit -ne 0) {
    Write-Host "P2R connector qualification did not pass (exit $dockerExit); preserved evidence remains non-authoritative" -ForegroundColor Red
    exit $dockerExit
}
if (-not (Test-Path -LiteralPath $receiptPath) -or $receipt.status -ne "ready") {
    throw "P2R connector qualification returned success without a ready receipt"
}
} finally {
    Remove-IsolatedWheelRoot -Path $isolatedWheelRoot
}
