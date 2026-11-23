# Export each slide of a .pptx to a PNG with PowerPoint itself (so the picture is exactly what PowerPoint shows).
#   powershell -File usability\export_slides.ps1 usability\TrailProof_final_slides_v3.pptx C:\some\folder
param([string]$Deck, [string]$OutDir)
$deckPath = (Resolve-Path $Deck).Path
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$app = New-Object -ComObject PowerPoint.Application
try {
    $pres = $app.Presentations.Open($deckPath, $true, $false, $false)   # read-only, no window
    $i = 0
    foreach ($slide in $pres.Slides) { $i++; $slide.Export((Join-Path (Resolve-Path $OutDir).Path ("slide$i.png")), "PNG", 1280, 720) }
    $pres.Close()
    "exported $i slides"
} finally {
    if ($app.Presentations.Count -eq 0) { $app.Quit() }
}
