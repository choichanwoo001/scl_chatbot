param(
    [string]$ProjectPath = (Get-Location).Path,
    [string]$OutputDirectory = "chat_logs"
)

$ErrorActionPreference = "Stop"

$resolvedProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path.TrimEnd('\')
$codexRoot = Join-Path $env:USERPROFILE ".codex"
$sessionsRoot = Join-Path $codexRoot "sessions"
$sessionIndexPath = Join-Path $codexRoot "session_index.jsonl"
$outputPath = Join-Path $resolvedProjectPath $OutputDirectory
$documentsPath = Join-Path $outputPath "documents"

if (-not (Test-Path -LiteralPath $sessionsRoot)) {
    throw "Codex session directory not found: $sessionsRoot"
}

$titleById = @{}
$updatedAtById = @{}
if (Test-Path -LiteralPath $sessionIndexPath) {
    Get-Content -LiteralPath $sessionIndexPath | ForEach-Object {
        try {
            $entry = $_ | ConvertFrom-Json
            if ($entry.id) {
                $titleById[$entry.id] = $entry.thread_name
                $updatedAtById[$entry.id] = $entry.updated_at
            }
        }
        catch {
            # Ignore malformed index entries; the session file remains authoritative.
        }
    }
}

$sessions = @()
Get-ChildItem -LiteralPath $sessionsRoot -Recurse -File -Filter "*.jsonl" | ForEach-Object {
    $sourceFile = $_
    $records = [System.Collections.Generic.List[object]]::new()

    Get-Content -LiteralPath $sourceFile.FullName | ForEach-Object {
        try {
            $records.Add(($_ | ConvertFrom-Json))
        }
        catch {
            # Keep exporting valid records if a partially written line is encountered.
        }
    }

    $metaRecord = $records | Where-Object { $_.type -eq "session_meta" } | Select-Object -First 1
    if (-not $metaRecord -or -not $metaRecord.payload.cwd) {
        return
    }

    $sessionCwd = [System.IO.Path]::GetFullPath([string]$metaRecord.payload.cwd).TrimEnd('\')
    if (-not $sessionCwd.Equals($resolvedProjectPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return
    }

    $sessionId = [string]$metaRecord.payload.id
    $startedAt = [datetimeoffset]$metaRecord.timestamp
    $title = if ($titleById.ContainsKey($sessionId) -and $titleById[$sessionId]) {
        [string]$titleById[$sessionId]
    }
    else {
        "Codex task $($sessionId.Substring(0, 8))"
    }

    $messages = [System.Collections.Generic.List[object]]::new()
    foreach ($record in $records) {
        if ($record.type -ne "response_item" -or $record.payload.type -ne "message") {
            continue
        }

        $role = [string]$record.payload.role
        if ($role -notin @("user", "assistant")) {
            continue
        }

        $parts = [System.Collections.Generic.List[string]]::new()
        foreach ($content in $record.payload.content) {
            if ($content.type -in @("input_text", "output_text") -and $content.text) {
                $text = [string]$content.text
                if ($role -eq "user" -and (
                    $text.TrimStart().StartsWith("<environment_context>") -or
                    $text.TrimStart().StartsWith("<recommended_plugins>")
                )) {
                    continue
                }
                $parts.Add($text.TrimEnd())
            }
            elseif ($content.type -match "image") {
                $parts.Add("[이미지 첨부]")
            }
            elseif ($content.type -match "audio") {
                $parts.Add("[오디오 첨부]")
            }
        }

        if ($parts.Count -eq 0) {
            continue
        }

        $messages.Add([pscustomobject]@{
            Timestamp = [datetimeoffset]$record.timestamp
            Role = $role
            Phase = [string]$record.payload.phase
            Text = ($parts -join "`n`n")
        })
    }

    $sessions += [pscustomobject]@{
        Id = $sessionId
        Title = $title
        StartedAt = $startedAt
        UpdatedAt = if ($updatedAtById.ContainsKey($sessionId)) { [string]$updatedAtById[$sessionId] } else { $null }
        SourceFile = $sourceFile.FullName
        Messages = $messages
    }
}

$sessions = @($sessions | Sort-Object StartedAt)
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
New-Item -ItemType Directory -Path $documentsPath -Force | Out-Null

$invalidFileNameChars = [System.IO.Path]::GetInvalidFileNameChars()
function ConvertTo-SafeFileName([string]$Value) {
    $safe = $Value
    foreach ($char in $invalidFileNameChars) {
        $safe = $safe.Replace([string]$char, "-")
    }
    $safe = ($safe -replace '\s+', '-').Trim('-')
    if ($safe.Length -gt 70) {
        $safe = $safe.Substring(0, 70).TrimEnd('-')
    }
    return $safe
}

$indexRows = [System.Collections.Generic.List[string]]::new()
foreach ($session in $sessions) {
    $datePrefix = $session.StartedAt.ToString("yyyy-MM-dd")
    $safeTitle = ConvertTo-SafeFileName $session.Title
    $shortId = $session.Id.Substring(0, 8)
    $fileName = "$datePrefix-$safeTitle-$shortId.md"
    $destination = Join-Path $documentsPath $fileName

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("# $($session.Title)")
    $lines.Add("")
    $lines.Add("- 작업 ID: ``$($session.Id)``")
    $lines.Add("- 시작: $($session.StartedAt.ToString('yyyy-MM-dd HH:mm:ss zzz'))")
    if ($session.UpdatedAt) {
        $lines.Add("- 마지막 갱신: $($session.UpdatedAt)")
    }
    $lines.Add("- 메시지 수: $($session.Messages.Count)")
    $lines.Add("")
    $lines.Add("> 사용자 메시지와 Codex의 사용자 표시 답변만 내보냈습니다. 시스템/개발자 지침, 추론, 도구 호출 및 도구 출력은 제외했습니다.")
    $lines.Add("")
    $lines.Add("## 대화")

    foreach ($message in $session.Messages) {
        $label = if ($message.Role -eq "user") {
            "사용자"
        }
        elseif ($message.Phase -eq "commentary") {
            "Codex · 진행 상황"
        }
        else {
            "Codex"
        }

        $lines.Add("")
        $lines.Add("### $label · $($message.Timestamp.ToString('yyyy-MM-dd HH:mm:ss zzz'))")
        $lines.Add("")
        $lines.Add($message.Text)
    }

    $lines.Add("")
    $lines.Add("---")
    $lines.Add("")
    $lines.Add("내보낸 시각: $([datetimeoffset]::Now.ToString('yyyy-MM-dd HH:mm:ss zzz'))")

    Set-Content -LiteralPath $destination -Value $lines -Encoding utf8
    $relativeFile = "documents/$($fileName.Replace(' ', '%20'))"
    $indexRows.Add("| $datePrefix | [$($session.Title)]($relativeFile) | $($session.Messages.Count) | ``$($session.Id)`` |")
}

$indexLines = [System.Collections.Generic.List[string]]::new()
$indexLines.Add("# Codex 대화 로그")
$indexLines.Add("")
$indexLines.Add("프로젝트: ``$resolvedProjectPath``")
$indexLines.Add("")
$indexLines.Add("내보낸 시각: $([datetimeoffset]::Now.ToString('yyyy-MM-dd HH:mm:ss zzz'))")
$indexLines.Add("")
$indexLines.Add("작업 수: $($sessions.Count)")
$indexLines.Add("")
$indexLines.Add("| 시작일 | 작업 | 메시지 수 | 작업 ID |")
$indexLines.Add("|---|---|---:|---|")
foreach ($row in $indexRows) {
    $indexLines.Add($row)
}
$indexLines.Add("")
$indexLines.Add("> 현재 실행 중인 작업은 내보내기 시점까지 기록된 메시지만 포함합니다. 이 스크립트를 다시 실행하면 최신 상태로 갱신됩니다.")

Set-Content -LiteralPath (Join-Path $outputPath "README.md") -Value $indexLines -Encoding utf8

[pscustomobject]@{
    ProjectPath = $resolvedProjectPath
    OutputPath = $outputPath
    DocumentsPath = $documentsPath
    SessionCount = $sessions.Count
    MessageCount = ($sessions | ForEach-Object { $_.Messages.Count } | Measure-Object -Sum).Sum
}
