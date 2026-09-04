[CmdletBinding()]
param(
    [Parameter()]
    [string]$OutputDirectory = "",

    [Parameter()]
    [switch]$AllowRepositoryOutput
)

$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Net.Http
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js is required for case-sensitive JSON validation"
}

if (-not $OutputDirectory) {
    $stamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
    $OutputDirectory = Join-Path ([IO.Path]::GetTempPath()) "openrouter-update-$stamp"
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
$resolvedParent = Split-Path -Parent $resolvedOutput
$repositoryRoot = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..\..\..")
)

function Test-PathWithin([string]$Candidate, [string]$Parent) {
    $normalizedParent = $Parent.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    return $Candidate.Equals($Parent, [StringComparison]::OrdinalIgnoreCase) -or
        $Candidate.StartsWith($normalizedParent, [StringComparison]::OrdinalIgnoreCase)
}

if (-not $AllowRepositoryOutput -and (Test-PathWithin $resolvedOutput $repositoryRoot)) {
    throw "Output directory must stay outside the repository unless -AllowRepositoryOutput is explicitly set: $resolvedOutput"
}
if (-not (Test-Path -LiteralPath $resolvedParent)) {
    New-Item -ItemType Directory -Path $resolvedParent | Out-Null
}
if (Test-Path -LiteralPath $resolvedOutput) {
    if ((Get-ChildItem -LiteralPath $resolvedOutput -Force | Measure-Object).Count -gt 0) {
        throw "Output directory must be absent or empty: $resolvedOutput"
    }
} else {
    New-Item -ItemType Directory -Path $resolvedOutput | Out-Null
}

$sources = @(
    [ordered]@{
        key = "models"
        file = "models.json"
        url = "https://openrouter.ai/api/v1/models?output_modalities=all&sort=newest&offset=0&limit=1000"
        count_path = "data"
    },
    [ordered]@{
        key = "images"
        file = "images.json"
        url = "https://openrouter.ai/api/v1/images/models"
        count_path = "data"
    },
    [ordered]@{
        key = "videos"
        file = "videos.json"
        url = "https://openrouter.ai/api/v1/videos/models"
        count_path = "data"
    },
    [ordered]@{
        key = "market"
        file = "market.json"
        url = "https://openrouter.ai/api/frontend/v1/models/find?active=true&fmt=cards"
        count_path = "data.models"
    }
)

$startedAt = [DateTimeOffset]::UtcNow
$manifestSources = @()
$partialPaths = @()
$httpClient = [System.Net.Http.HttpClient]::new()
$httpClient.Timeout = [TimeSpan]::FromSeconds(120)
$null = $httpClient.DefaultRequestHeaders.TryAddWithoutValidation(
    "Accept",
    "application/json"
)
$null = $httpClient.DefaultRequestHeaders.TryAddWithoutValidation(
    "Referer",
    "https://openrouter.ai/models"
)
$null = $httpClient.DefaultRequestHeaders.TryAddWithoutValidation(
    "User-Agent",
    "ModelMirror-OpenRouter-Audit/1.0"
)
try {
    foreach ($source in $sources) {
        $partialPath = Join-Path $resolvedOutput ($source.file + ".partial")
        $partialPaths += $partialPath
        $response = $httpClient.GetAsync($source.url).GetAwaiter().GetResult()
        try {
            $statusCode = [int]$response.StatusCode
            if ($statusCode -ne 200) {
                throw "$($source.key) returned HTTP $statusCode"
            }
            $contentBytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
            [IO.File]::WriteAllBytes($partialPath, $contentBytes)
        } finally {
            $response.Dispose()
        }
        $inspectorPath = Join-Path $PSScriptRoot "inspect-json.mjs"
        $recordCountText = & node $inspectorPath $partialPath $source.count_path $source.key
        if ($LASTEXITCODE -ne 0) {
            throw "$($source.key) response has no expected records"
        }
        $recordCount = [int]$recordCountText
        $hash = (Get-FileHash -LiteralPath $partialPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $manifestSources += [ordered]@{
            key = $source.key
            file = $source.file
            url = $source.url
            http_status = $statusCode
            count_path = $source.count_path
            records = $recordCount
            bytes = (Get-Item -LiteralPath $partialPath).Length
            sha256 = $hash
            fetched_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
    }

    $inspectorPath = Join-Path $PSScriptRoot "inspect-json.mjs"
    $coverageLines = & node $inspectorPath `
        (Join-Path $resolvedOutput "models.json.partial") data compare-market `
        (Join-Path $resolvedOutput "market.json.partial")
    $coverageExit = $LASTEXITCODE
    try {
        $coverage = ($coverageLines -join [Environment]::NewLine) | ConvertFrom-Json
    } catch {
        throw "Unable to validate exact model-market coverage"
    }
    if ($coverageExit -ne 0 -or -not [bool]$coverage.complete) {
        $missing = @($coverage.missing_from_market) -join ","
        $extra = @($coverage.market_only) -join ","
        throw "Market response coverage is incomplete: missing_from_market=[$missing], market_only=[$extra]"
    }
    $sourceNonBatchCount = [int]$coverage.source_non_batch_models
    $marketUniqueBaseCount = [int]$coverage.market_unique_base_models

    foreach ($source in $sources) {
        Move-Item -LiteralPath (Join-Path $resolvedOutput ($source.file + ".partial")) -Destination (Join-Path $resolvedOutput $source.file)
    }
    $manifest = [ordered]@{
        schema_version = 1
        status = "complete"
        request_started_at = $startedAt.ToString("o")
        request_finished_at = [DateTimeOffset]::UtcNow.ToString("o")
        cross_source_coverage = [ordered]@{
            comparison = [string]$coverage.comparison
            source_non_batch_models = $sourceNonBatchCount
            market_unique_base_models = $marketUniqueBaseCount
            missing_from_market = @($coverage.missing_from_market)
            market_only = @($coverage.market_only)
            complete = $true
        }
        sources = $manifestSources
    }
    $manifestPath = Join-Path $resolvedOutput "manifest.json"
    [IO.File]::WriteAllText(
        $manifestPath,
        ($manifest | ConvertTo-Json -Depth 8),
        $utf8NoBom
    )
    Write-Output $manifestPath
} catch {
    foreach ($path in $partialPaths) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }
    throw
} finally {
    $httpClient.Dispose()
}
