#requires -Version 7.0
param([string] $Engine = '', [string] $FFmpeg = '')

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $Engine) {
    $Engine = Join-Path $root '.venv/Scripts/content-engine.exe'
    if (-not (Test-Path -LiteralPath $Engine)) { $Engine = 'content-engine' }
}
if (-not $FFmpeg) {
    $FFmpeg = $env:CONTENT_ENGINE_FFMPEG
    if (-not $FFmpeg) {
        $FFmpeg = Get-ChildItem -Path (Join-Path $root '.tools/ffmpeg-*/bin/ffmpeg.exe') |
            Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $FFmpeg) { $FFmpeg = 'ffmpeg' }
}
$FFmpeg = (Get-Command $FFmpeg -CommandType Application).Source

Add-Type -AssemblyName System.Speech
$speaker = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $speaker.SelectVoice('Microsoft An')
    $created = & $Engine --root $root project create --channel wildlife_vi --topic 'Bản thử quy trình dựng video'
    if ($LASTEXITCODE) { throw 'Project creation failed' }
    $project = $created | ConvertFrom-Json
    $cards = @(
        @{ size = '540x960'; rate = 24; color = '0x142438'; accent = '0x88E0BF'; title = 'SOURCE CLIPS'
            narration = 'Mỗi cảnh bắt đầu từ một đoạn phim đã chọn.' },
        @{ size = '640x960'; rate = 25; color = '0x30213D'; accent = '0xF2B8D6'; title = 'VOICE TRACK'
            narration = 'Engine ghép lời đọc với hình ảnh theo timeline.' },
        @{ size = '540x960'; rate = 30; color = '0x15383B'; accent = '0xF7D99B'; title = 'LOCAL PREVIEW'
            narration = 'Bản dựng có phụ đề và sẵn sàng để bạn xem.' }
    )
    $assets = @()
    $scenes = @()
    Push-Location -LiteralPath (Join-Path $env:WINDIR 'Fonts')
    try {
        for ($i = 0; $i -lt $cards.Count; $i++) {
            $card = $cards[$i]
            $number = '{0:00}' -f ($i + 1)
            $clip = "media/clip_$number.mp4"
            $audio = "audio/scene_$number.wav"
            $background = "gradients=s=$($card.size):r=$($card.rate):d=8:c0=$($card.color):c1=0x0C131F:speed=0.12"
            $filters = @(
                'drawgrid=w=54:h=54:t=1:c=white@0.07'
                'drawbox=x=36:y=310:w=82:h=4:c=white@0.65:t=fill'
                'drawtext=fontfile=arial.ttf:text=CONTENT ENGINE   /   DEMO:x=36:y=268:fontsize=18:fontcolor=white'
                "drawtext=fontfile=arialbd.ttf:text=$($number)/03:x=35:y=348:fontsize=82:fontcolor=$($card.accent)"
                "drawtext=fontfile=arialbd.ttf:text=$($card.title):x=37:y=465:fontsize=33:fontcolor=white"
            ) -join ','
            $ffArgs = @(
                '-hide_banner', '-loglevel', 'error', '-nostdin', '-filter_threads', '2',
                '-f', 'lavfi', '-i', $background, '-vf', $filters, '-c:v', 'libx264',
                '-preset', 'veryfast', '-threads:v', '3', '-pix_fmt', 'yuv420p',
                (Join-Path $project.directory $clip)
            )
            & $FFmpeg @ffArgs
            if ($LASTEXITCODE) { throw "Failed to create $clip" }
            $speaker.SetOutputToWaveFile((Join-Path $project.directory $audio))
            $speaker.Speak($card.narration)
            $speaker.SetOutputToNull()
            $assets += @{
                asset_id = "clip_$number"; local_path = $clip; source = 'original'; creator = 'Content Engine demo'
                license = @{
                    status = 'verified'; name = 'Original generated demo clip'
                    checked_at = (Get-Date -Format 'yyyy-MM-dd'); attribution_required = $false
                }
            }
            $scenes += @{
                id = "scene_$number"; asset_id = "clip_$number"; source_start = 0.5; source_end = 7.5
                narration = $card.narration; narration_audio = $audio; subtitle = $true; transition = 'cut'
            }
        }
    }
    finally { Pop-Location }
}
finally { $speaker.Dispose() }

@{ schema_version = 1; assets = $assets } | ConvertTo-Json -Depth 8 |
    Set-Content -Encoding utf8 -LiteralPath (Join-Path $project.directory 'assets.json')
@{ schema_version = 1; project_id = $project.project_id; format = $project.format; scenes = $scenes } |
    ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 -LiteralPath (Join-Path $project.directory 'timeline.json')

$previousFFmpeg = $env:CONTENT_ENGINE_FFMPEG
try {
    $env:CONTENT_ENGINE_FFMPEG = $FFmpeg
    & $Engine --root $root timeline validate $project.project_id
    if ($LASTEXITCODE) { throw 'Timeline validation failed' }
    & $Engine --root $root render $project.project_id
    if ($LASTEXITCODE) { throw 'Demo render failed' }
}
finally { $env:CONTENT_ENGINE_FFMPEG = $previousFFmpeg }
