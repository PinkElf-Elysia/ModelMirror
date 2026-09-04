[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,

    [Parameter(Mandatory = $true)]
    [string]$SnapshotDirectory,

    [Parameter()]
    [string]$OutputDirectory = "",

    [Parameter()]
    [switch]$AllowDrift,

    [Parameter()]
    [switch]$IncludeVolatileAsDrift
)

$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$repo = [IO.Path]::GetFullPath($RepositoryRoot)
$snapshot = [IO.Path]::GetFullPath($SnapshotDirectory)
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $snapshot "audit"
}
$output = [IO.Path]::GetFullPath($OutputDirectory)

function Test-PathWithin([string]$Candidate, [string]$Parent) {
    $normalizedParent = $Parent.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    return $Candidate.Equals($Parent, [StringComparison]::OrdinalIgnoreCase) -or
        $Candidate.StartsWith($normalizedParent, [StringComparison]::OrdinalIgnoreCase)
}

if (Test-PathWithin $snapshot $repo) {
    throw "Snapshot directory must stay outside the repository: $snapshot"
}
if (Test-PathWithin $output $repo) {
    throw "Audit output directory must stay outside the repository: $output"
}

foreach ($required in @(
    "client/src/data/models.ts",
    "scripts/audit-model-modalities.mjs",
    "scripts/audit-openrouter-classifications.mjs",
    "scripts/check-multimodal-readiness.mjs",
    "docs/multimodal-readiness.json"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $repo $required))) {
        throw "Repository prerequisite is missing: $required"
    }
}

$manifestPath = Join-Path $snapshot "manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Complete manifest is required: $manifestPath"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or $manifest.status -ne "complete") {
    throw "Snapshot manifest is incomplete"
}
function ConvertTo-EvidenceTime([object]$Value, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace([string]$Value)) {
        throw "Snapshot manifest time is missing: $Label"
    }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse([string]$Value, [ref]$parsed)) {
        throw "Snapshot manifest time is invalid: $Label"
    }
    return $parsed
}

$windowStartedAt = ConvertTo-EvidenceTime $manifest.request_started_at "request_started_at"
$windowFinishedAt = ConvertTo-EvidenceTime $manifest.request_finished_at "request_finished_at"
if ($windowFinishedAt -lt $windowStartedAt) {
    throw "Snapshot manifest evidence window ends before it starts"
}
$expectedSources = @(
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
if (@($manifest.sources).Count -ne $expectedSources.Count) {
    throw "Snapshot manifest must contain exactly four sources"
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js is required to run repository audit scripts"
}
$inspectorPath = Join-Path $PSScriptRoot "inspect-json.mjs"
foreach ($expected in $expectedSources) {
    $matches = @($manifest.sources | Where-Object { $_.key -eq $expected.key })
    if ($matches.Count -ne 1) {
        throw "Snapshot manifest must contain exactly one $($expected.key) source"
    }
    $source = $matches[0]
    if (
        $source.file -ne $expected.file -or
        $source.url -ne $expected.url -or
        $source.count_path -ne $expected.count_path -or
        [int]$source.http_status -ne 200 -or
        [int]$source.records -le 0
    ) {
        throw "Snapshot manifest metadata mismatch: $($expected.key)"
    }
    $sourceFetchedAt = ConvertTo-EvidenceTime $source.fetched_at "$($expected.key).fetched_at"
    if ($sourceFetchedAt -lt $windowStartedAt -or $sourceFetchedAt -gt $windowFinishedAt) {
        throw "Snapshot source falls outside the evidence window: $($expected.key)"
    }
    $sourcePath = Join-Path $snapshot $expected.file
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Snapshot file is missing: $($expected.file)"
    }
    $actualHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne [string]$source.sha256) {
        throw "Snapshot hash mismatch: $($expected.file)"
    }
    if ((Get-Item -LiteralPath $sourcePath).Length -ne [long]$source.bytes) {
        throw "Snapshot byte count mismatch: $($expected.file)"
    }
    $actualRecordCount = & node $inspectorPath $sourcePath $expected.count_path $expected.key
    if ($LASTEXITCODE -ne 0 -or [int]$actualRecordCount -ne [int]$source.records) {
        throw "Snapshot record count mismatch: $($expected.file)"
    }
}
if (Test-Path -LiteralPath $output) {
    if ((Get-ChildItem -LiteralPath $output -Force | Measure-Object).Count -gt 0) {
        throw "Audit output directory must be absent or empty: $output"
    }
} else {
    New-Item -ItemType Directory -Path $output | Out-Null
}
$incompletePath = Join-Path $output "INCOMPLETE.json"
[IO.File]::WriteAllText(
    $incompletePath,
    ([ordered]@{
        status = "incomplete"
        started_at = [DateTimeOffset]::UtcNow.ToString("o")
        evidence_manifest = $manifestPath
    } | ConvertTo-Json),
    $utf8NoBom
)

$modelsPath = Join-Path $snapshot "models.json"
$imagesPath = Join-Path $snapshot "images.json"
$videosPath = Join-Path $snapshot "videos.json"
$marketPath = Join-Path $snapshot "market.json"
$modalitiesPath = Join-Path $output "modalities.json"
$classificationsPath = Join-Path $output "classifications.json"
$readinessPath = Join-Path $output "readiness.json"
$actionablePath = Join-Path $output "actionable.json"
$summaryPath = Join-Path $output "summary.json"

$coverageLines = & node $inspectorPath $modelsPath data compare-market $marketPath
$coverageExit = $LASTEXITCODE
try {
    $exactMarketCoverage = ($coverageLines -join [Environment]::NewLine) | ConvertFrom-Json
} catch {
    throw "Unable to validate exact model-market coverage"
}
if (
    $coverageExit -ne 0 -or
    [string]$exactMarketCoverage.comparison -ne "exact-id-set-v1" -or
    -not [bool]$exactMarketCoverage.complete
) {
    $missing = @($exactMarketCoverage.missing_from_market) -join ","
    $extra = @($exactMarketCoverage.market_only) -join ","
    throw "Market source coverage is incomplete: missing_from_market=[$missing], market_only=[$extra]"
}

Push-Location $repo
try {
    $modalitiesLines = & node "scripts/audit-model-modalities.mjs" `
        --catalog $modelsPath `
        --image-models $imagesPath `
        --video-models $videosPath
    $modalitiesExit = $LASTEXITCODE
    $modalitiesText = ($modalitiesLines -join [Environment]::NewLine) + [Environment]::NewLine
    [IO.File]::WriteAllText($modalitiesPath, $modalitiesText, $utf8NoBom)

    & node "scripts/audit-openrouter-classifications.mjs" `
        --models $modelsPath `
        --images $imagesPath `
        --videos $videosPath `
        --market $marketPath `
        --output $classificationsPath | Out-Null
    $classificationsExit = $LASTEXITCODE

    $readinessLines = & node "scripts/check-multimodal-readiness.mjs" "docs/multimodal-readiness.json"
    $readinessExit = $LASTEXITCODE
    $readinessText = ($readinessLines -join [Environment]::NewLine) + [Environment]::NewLine
    [IO.File]::WriteAllText($readinessPath, $readinessText, $utf8NoBom)
} finally {
    Pop-Location
}

$modalities = Get-Content -LiteralPath $modalitiesPath -Raw | ConvertFrom-Json
$classifications = Get-Content -LiteralPath $classificationsPath -Raw | ConvertFrom-Json
try {
    $readiness = Get-Content -LiteralPath $readinessPath -Raw | ConvertFrom-Json
    if (
        $readinessExit -ne 0 -or
        $null -eq $readiness -or
        $readiness.status -ne "ok" -or
        $null -eq $readiness.catalog -or
        $null -eq $readiness.catalog.live -or
        $null -eq $readiness.catalog.onsite_openrouter
    ) {
        throw "invalid readiness result"
    }
} catch {
    throw "Readiness check did not produce valid JSON (exit $readinessExit)"
}
$marketUniqueModels = [int]$exactMarketCoverage.market_unique_base_models
$sourceNonBatchModels = [int]$exactMarketCoverage.source_non_batch_models
$marketComparedModels = [int]$classifications.openrouter_models_sidebar.compared_models
$localComparedModels = [int]$classifications.coverage.local_catalog_entries_compared
if (
    [int]$classifications.openrouter_models_sidebar.source_model_snapshots -ne $marketUniqueModels -or
    [int]$classifications.source.non_batch_entries -ne $sourceNonBatchModels -or
    $marketComparedModels -ne $localComparedModels
) {
    throw "Audit coverage disagrees with exact source coverage: market/source=$marketUniqueModels/$sourceNonBatchModels, compared/local=$marketComparedModels/$localComparedModels"
}
$structuralFields = @(
    "series", "author", "providers", "categories", "discounted",
    "distillable", "zero_data_retention", "regions", "created_at"
)
$volatileFields = @("tool_call_success_rate", "artificial_analysis", "design_arena")
$marketStructuralModels = New-Object System.Collections.Generic.HashSet[string]
$marketVolatileModels = New-Object System.Collections.Generic.HashSet[string]
$marketFieldCounts = @{}
foreach ($mismatch in @($classifications.openrouter_models_sidebar.snapshot_mismatches)) {
    foreach ($field in @($structuralFields + $volatileFields)) {
        $actualValue = $mismatch.actual.$field | ConvertTo-Json -Depth 12 -Compress
        $expectedValue = $mismatch.expected.$field | ConvertTo-Json -Depth 12 -Compress
        if ($actualValue -ne $expectedValue) {
            $marketFieldCounts[$field] = 1 + [int]($marketFieldCounts[$field])
            if ($structuralFields -contains $field) {
                $null = $marketStructuralModels.Add([string]$mismatch.id)
            }
            if ($volatileFields -contains $field) {
                $null = $marketVolatileModels.Add([string]$mismatch.id)
            }
        }
    }
}
$marketBothModels = @(
    $marketStructuralModels |
        Where-Object { $marketVolatileModels.Contains($_) } |
        Sort-Object
)
$marketStructuralOnlyModels = @(
    $marketStructuralModels |
        Where-Object { -not $marketVolatileModels.Contains($_) } |
        Sort-Object
)
$marketVolatileOnlyModels = @(
    $marketVolatileModels |
        Where-Object { -not $marketStructuralModels.Contains($_) } |
        Sort-Object
)

$metadataFieldCounts = @{}
$metadataStructuralModels = New-Object System.Collections.Generic.HashSet[string]
$metadataEditorialModels = New-Object System.Collections.Generic.HashSet[string]
foreach ($mismatch in @($modalities.metadata_mismatches)) {
    foreach ($field in @($mismatch.fields)) {
        $metadataFieldCounts[$field] = 1 + [int]($metadataFieldCounts[$field])
        if ($field -eq "raw_description") {
            $null = $metadataEditorialModels.Add([string]$mismatch.id)
        } else {
            $null = $metadataStructuralModels.Add([string]$mismatch.id)
        }
    }
}
$batchStructuralModels = New-Object System.Collections.Generic.HashSet[string]
$batchEditorialModels = New-Object System.Collections.Generic.HashSet[string]
foreach ($mismatch in @($modalities.batch_metadata_mismatches)) {
    foreach ($field in @($mismatch.fields)) {
        if ($field -eq "raw_description") {
            $null = $batchEditorialModels.Add([string]$mismatch.id)
        } else {
            $null = $batchStructuralModels.Add([string]$mismatch.id)
        }
    }
}

if (
    $classifications.schema_version -ne 1 -or
    $null -eq $classifications.actionability -or
    $null -eq $classifications.actionability.actionable -or
    $null -eq $classifications.actionability.volatile_market_observation_ids
) {
    throw "Classification audit is missing its versioned actionability result"
}
$classificationActionableDrift = [bool]$classifications.actionability.actionable
$classificationExitOnlyFromVolatileMarket = (
    $classificationsExit -ne 0 -and
    -not $classificationActionableDrift -and
    @($classifications.actionability.volatile_market_observation_ids).Count -gt 0
)
if (
    $classificationsExit -ne 0 -and
    -not $classificationActionableDrift -and
    -not $classificationExitOnlyFromVolatileMarket
) {
    throw "Classification audit exited nonzero without an explicit actionable or volatile reason"
}
$hasActionableDrift = (
    $modalitiesExit -ne 0 -or
    $classificationActionableDrift -or
    $readinessExit -ne 0
)
$hasDrift = (
    $hasActionableDrift -or
    ($IncludeVolatileAsDrift -and $marketVolatileModels.Count -gt 0)
)
$actionableState = [ordered]@{
    schema_version = 1
    model_catalog = [ordered]@{
        missing_live_models = @($modalities.missing_live_models)
        metadata_mismatches = @($modalities.metadata_mismatches)
        missing_batch_variants = @($modalities.missing_batch_variants)
        stale_batch_variants = @($modalities.stale_batch_variants)
        orphan_batch_variants = @($modalities.orphan_batch_variants)
        batch_metadata_mismatches = @($modalities.batch_metadata_mismatches)
        uncertain_status_mismatches = @($modalities.uncertain_status_mismatches)
        image_api_missing_from_snapshot = @($modalities.image_api_missing_from_snapshot)
        video_api_missing_from_snapshot = @($modalities.video_api_missing_from_snapshot)
        general_video_output_without_dedicated_api = @($modalities.general_video_output_without_dedicated_api)
    }
    classifications = $classifications.actionability.reasons
    readiness = [ordered]@{
        exit_code = $readinessExit
        status = $readiness.status
    }
}
[IO.File]::WriteAllText(
    $actionablePath,
    ($actionableState | ConvertTo-Json -Depth 20),
    $utf8NoBom
)
$signatureScript = Join-Path $PSScriptRoot "stable-signature.mjs"
$actionableSignature = & node $signatureScript $actionablePath
if ($LASTEXITCODE -ne 0 -or $actionableSignature -notmatch '^[0-9a-f]{64}$') {
    throw "Unable to compute the actionable drift signature"
}
$summary = [ordered]@{
    schema_version = 1
    audited_at = [DateTimeOffset]::UtcNow.ToString("o")
    evidence_manifest = $manifestPath
    evidence_window = [ordered]@{
        started_at = $manifest.request_started_at
        finished_at = $manifest.request_finished_at
    }
    cross_source_coverage = $exactMarketCoverage
    status = $(if ($hasDrift) { "drift" } else { "clean" })
    drift_policy = [ordered]@{
        actionable_drift = $hasActionableDrift
        include_volatile_market = [bool]$IncludeVolatileAsDrift
        classification_exit_only_from_volatile_market = $classificationExitOnlyFromVolatileMarket
        volatile_market_observations = ($marketVolatileModels.Count -gt 0)
    }
    monitor = [ordered]@{
        actionable_signature_algorithm = "sha256-canonical-json-v1"
        actionable_signature = $actionableSignature
        notify_when_signature_changes = $true
    }
    exit_codes = [ordered]@{
        modalities = $modalitiesExit
        classifications = $classificationsExit
        readiness = $readinessExit
    }
    counts = [ordered]@{
        local_snapshot = $modalities.snapshot_total
        source_catalog_entries = $modalities.catalog_total
        source_non_batch_models = $modalities.live_snapshot_total
        local_batch_variants = $modalities.serving_variant_total
        batch_counted_in_snapshot = $modalities.serving_variants_counted_in_snapshot
        local_live = $readiness.catalog.live
        local_uncertain = $readiness.catalog.uncertain
        local_expired = $readiness.catalog.expired
        local_onsite_openrouter = $readiness.catalog.onsite_openrouter
        local_adaptation_denominator = $readiness.catalog.adaptation_denominator
        source_image_models = $modalities.dedicated_catalogs.image_generation
        source_video_models = $modalities.dedicated_catalogs.video_generation
        market_unique_base_models = $marketUniqueModels
        market_compared_models = $marketComparedModels
    }
    drift = [ordered]@{
        missing_live_models = @($modalities.missing_live_models)
        metadata_mismatch_count = @($modalities.metadata_mismatches).Count
        metadata_field_counts = $metadataFieldCounts
        metadata_structural_model_count = $metadataStructuralModels.Count
        metadata_structural_model_ids = @($metadataStructuralModels | Sort-Object)
        metadata_editorial_model_count = $metadataEditorialModels.Count
        metadata_editorial_model_ids = @($metadataEditorialModels | Sort-Object)
        missing_batch_variants = @($modalities.missing_batch_variants)
        stale_batch_variants = @($modalities.stale_batch_variants)
        orphan_batch_variants = @($modalities.orphan_batch_variants)
        batch_metadata_mismatches = @($modalities.batch_metadata_mismatches)
        batch_structural_model_count = $batchStructuralModels.Count
        batch_structural_model_ids = @($batchStructuralModels | Sort-Object)
        batch_editorial_model_count = $batchEditorialModels.Count
        batch_editorial_model_ids = @($batchEditorialModels | Sort-Object)
        uncertain_status_mismatches = @($modalities.uncertain_status_mismatches)
        operation_mismatches = @($classifications.authoritative_classification.operation_mismatches)
        job_capability_mismatches = @($classifications.authoritative_classification.job_capability_mismatches)
        specialized = [ordered]@{
            image_api_missing_from_snapshot = @($modalities.image_api_missing_from_snapshot)
            video_api_missing_from_snapshot = @($modalities.video_api_missing_from_snapshot)
            general_image_output_without_dedicated_api = @($modalities.general_image_output_without_dedicated_api)
            general_video_output_without_dedicated_api = @($modalities.general_video_output_without_dedicated_api)
            protected_metadata_exceptions = @($modalities.specialized_metadata_exceptions)
        }
        pricing_basis = [ordered]@{
            non_explicit_models_marked_free = $classifications.pricing_taxonomy.non_explicit_models_marked_free
            zero_token_media_with_wrong_basis = $classifications.pricing_taxonomy.zero_token_media_with_wrong_basis
            zero_token_request_with_wrong_basis = $classifications.pricing_taxonomy.zero_token_request_with_wrong_basis
            audio_hour_pricing_overlay_mismatches = $classifications.pricing_taxonomy.audio_hour_pricing_overlay_mismatches
            dedicated_paid_video_models_marked_free = $classifications.pricing_taxonomy.dedicated_paid_video_models_marked_free
            image_models_requiring_endpoint_pricing_but_marked_free = $classifications.pricing_taxonomy.image_models_requiring_endpoint_pricing_but_marked_free
        }
        market_mismatch_count = @($classifications.openrouter_models_sidebar.snapshot_mismatches).Count
        market_structural_model_count = $marketStructuralModels.Count
        market_structural_model_ids = @($marketStructuralModels | Sort-Object)
        market_volatile_model_count = $marketVolatileModels.Count
        market_volatile_model_ids = @($marketVolatileModels | Sort-Object)
        market_structural_only_model_count = $marketStructuralOnlyModels.Count
        market_structural_only_model_ids = $marketStructuralOnlyModels
        market_volatile_only_model_count = $marketVolatileOnlyModels.Count
        market_volatile_only_model_ids = $marketVolatileOnlyModels
        market_structural_and_volatile_model_count = $marketBothModels.Count
        market_structural_and_volatile_model_ids = $marketBothModels
        market_field_counts = $marketFieldCounts
    }
    artifacts = [ordered]@{
        modalities = $modalitiesPath
        classifications = $classificationsPath
        readiness = $readinessPath
        actionable = $actionablePath
    }
}
[IO.File]::WriteAllText(
    $summaryPath,
    ($summary | ConvertTo-Json -Depth 20),
    $utf8NoBom
)
Remove-Item -LiteralPath $incompletePath -Force
Write-Output $summaryPath

if ($hasDrift -and -not $AllowDrift) {
    exit 2
}
