# linecrafty setup: finds ComfyUI, downloads + verifies the models, installs the SVG tracer.
# Safe to re-run any time (skips what is already done, resumes interrupted downloads).
param(
    [string]$ComfyPath,      # optional: your ComfyUI folder (the one containing main.py, or its parent)
    [switch]$Yes,            # don't ask before downloading
    [string]$InstallDir      # optional: set up a different linecrafty folder (default: this script's folder)
)
# 'Continue' on purpose: in Windows PowerShell 5.1, 'Stop' turns any stderr text from python/curl
# into a fatal error. Failures are checked explicitly instead ($LASTEXITCODE, Test-Path).
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$Root = if ($InstallDir) { (Resolve-Path $InstallDir).Path } else { $PSScriptRoot }

$Models = @(
    @{ folder = 'diffusion_models'; name = 'flux1-dev-kontext_fp8_scaled.safetensors'; size = 11904640136
       sha256 = '630ba795ec64283b4230ea23cf79406c2c68b7c578229ed139f30043eadb30a2'
       url = 'https://huggingface.co/Comfy-Org/Flux-1-Kontext-Dev/resolve/main/split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors' },
    @{ folder = 'text_encoders'; name = 't5xxl_fp8_e4m3fn_scaled.safetensors'; size = 5157348688
       sha256 = 'a498f0485dc9536735258018417c3fd7758dc3bccc0a645feaa472b34955557a'
       url = 'https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn_scaled.safetensors' },
    @{ folder = 'text_encoders'; name = 'clip_l.safetensors'; size = 246144152
       sha256 = '660c6f5b1abae9dc498ac2d21e1347d2abdb0cf6c0c0c8576cd796491d9a6cdd'
       url = 'https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors' },
    @{ folder = 'vae'; name = 'ae.safetensors'; size = 335304388
       sha256 = 'afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38'
       url = 'https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/resolve/main/split_files/vae/ae.safetensors' },
    @{ folder = 'upscale_models'; name = '4x-AnimeSharp.pth'; size = 67010245
       sha256 = 'e7a7de2dafd7331c1992862bbbcd9e9712a9f9f8e6303f0aaa59b4341d359bab'
       url = 'https://huggingface.co/Kim2091/AnimeSharp/resolve/main/4x-AnimeSharp.pth' }
)

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "   $m" -ForegroundColor Green }
function Info($m) { Write-Host "   $m" }
function Warn($m) { Write-Host "   WARNING: $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host ""; Write-Host "SETUP STOPPED: $m" -ForegroundColor Red; exit 1 }
function Ask($q, $default = 'y') {
    if ($Yes) { return $true }
    $hint = if ($default -eq 'y') { '[Y/n]' } else { '[y/N]' }
    $a = Read-Host "   $q $hint"
    if ([string]::IsNullOrWhiteSpace($a)) { $a = $default }
    return $a.Trim().ToLower().StartsWith('y')
}
function GB($bytes) { '{0:N1} GB' -f ($bytes / 1GB) }

# Files from a downloaded zip are "blocked" by Windows; unblock ours so they run without prompts.
Get-ChildItem $Root -Recurse -File | Unblock-File -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "  Linecrafty - setup" -ForegroundColor White
Write-Host "  ---------------------------"

# ------------------------------------------------------------------ 1. find ComfyUI
Step "Looking for ComfyUI"

function Resolve-ComfyDir([string]$path) {
    if ([string]::IsNullOrWhiteSpace($path)) { return $null }
    $path = $path.Trim().Trim('"')
    foreach ($d in @($path, (Join-Path $path 'ComfyUI'), (Join-Path $path 'resources\ComfyUI'))) {
        if ((Test-Path (Join-Path $d 'main.py')) -and (Test-Path (Join-Path $d 'comfy'))) { return (Resolve-Path $d).Path }
    }
    return $null
}
function Find-Python([string]$comfyDir) {
    foreach ($rel in @('.venv\Scripts\python.exe', 'venv\Scripts\python.exe', '..\python_embeded\python.exe',
                       '..\.venv\Scripts\python.exe', '..\venv\Scripts\python.exe')) {
        $p = Join-Path $comfyDir $rel
        if (Test-Path $p) { return (Resolve-Path $p).Path }
    }
    return $null
}

$candidates = @()
if ($ComfyPath) {
    $d = Resolve-ComfyDir $ComfyPath
    if (-not $d) { Fail "No ComfyUI found in '$ComfyPath' (looked for main.py)." }
    $candidates += [pscustomobject]@{ Label = 'given path'; Dir = $d; Python = (Find-Python $d); Extra = @() }
} else {
    # Comfy Desktop (current app)
    $cd = Join-Path $env:APPDATA 'Comfy Desktop'
    if (Test-Path "$cd\installations.json") {
        foreach ($inst in (Get-Content "$cd\installations.json" -Raw | ConvertFrom-Json)) {
            if (-not $inst.installPath) { continue }
            $d = Resolve-ComfyDir $inst.installPath
            if (-not $d) { continue }
            $extra = @()
            $yaml = Join-Path $cd "instance-model-paths\$($inst.id).yaml"
            if (Test-Path $yaml) {
                foreach ($m in [regex]::Matches((Get-Content $yaml -Raw), "(?m)^\s*base_path:\s*'?([^'\r\n]+)'?")) {
                    $v = $m.Groups[1].Value.Trim()
                    if (Test-Path $v) { $extra += $v }
                }
            }
            $candidates += [pscustomobject]@{ Label = "Comfy Desktop ($($inst.name))"; Dir = $d; Python = (Find-Python $d); Extra = $extra }
        }
    }
    # Older ComfyUI desktop app
    $old = Join-Path $env:APPDATA 'ComfyUI\config.json'
    if (Test-Path $old) {
        $base = (Get-Content $old -Raw | ConvertFrom-Json).basePath
        $code = Get-ChildItem "$env:LOCALAPPDATA\Programs" -Directory -ErrorAction SilentlyContinue |
                ForEach-Object { Resolve-ComfyDir (Join-Path $_.FullName 'resources\ComfyUI') } | Select-Object -First 1
        if ($base -and $code -and (Test-Path "$base\.venv\Scripts\python.exe")) {
            $candidates += [pscustomobject]@{ Label = 'ComfyUI desktop app'; Dir = $code
                Python = (Resolve-Path "$base\.venv\Scripts\python.exe").Path; Extra = @("$base\models") }
        }
    }
    # Portable builds in common places
    foreach ($p in @("C:\ComfyUI_windows_portable", "$HOME\ComfyUI_windows_portable", "$HOME\Desktop\ComfyUI_windows_portable",
                     "$HOME\Downloads\ComfyUI_windows_portable", "D:\ComfyUI_windows_portable")) {
        $d = Resolve-ComfyDir $p
        if ($d) { $candidates += [pscustomobject]@{ Label = 'portable'; Dir = $d; Python = (Find-Python $d); Extra = @() } }
    }
}

$pick = $null
if ($candidates.Count -eq 1) {
    $pick = $candidates[0]
    Ok "Found: $($pick.Label) - $($pick.Dir)"
} elseif ($candidates.Count -gt 1) {
    Info "Found more than one ComfyUI:"
    for ($i = 0; $i -lt $candidates.Count; $i++) { Info "  $($i + 1)) $($candidates[$i].Label) - $($candidates[$i].Dir)" }
    $n = if ($Yes) { '1' } else { Read-Host "   Which one? [1]" }
    if ([string]::IsNullOrWhiteSpace($n)) { $n = '1' }
    $pick = $candidates[[int]$n - 1]
} else {
    Info "Couldn't find ComfyUI automatically."
    Info "Install it from https://www.comfy.org/download if you haven't (the Desktop app is easiest),"
    Info "or tell me where it is."
    $p = Read-Host "   Paste the path to your ComfyUI folder (or press Enter to browse)"
    if ([string]::IsNullOrWhiteSpace($p)) {
        Add-Type -AssemblyName System.Windows.Forms
        $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
        $dlg.Description = 'Select your ComfyUI folder (the one that contains main.py)'
        if ($dlg.ShowDialog() -eq 'OK') { $p = $dlg.SelectedPath }
    }
    $d = Resolve-ComfyDir $p
    if (-not $d) { Fail "No ComfyUI found there (looked for main.py). Re-run setup and pick the ComfyUI folder." }
    $pick = [pscustomobject]@{ Label = 'chosen'; Dir = $d; Python = (Find-Python $d); Extra = @() }
}

if (-not $pick.Python) {
    Info "Couldn't find the Python that ComfyUI uses (no .venv or python_embeded next to it)."
    $p = (Read-Host "   Paste the full path to ComfyUI's python.exe").Trim().Trim('"')
    if (-not (Test-Path $p)) { Fail "python.exe not found at '$p'." }
    $pick.Python = (Resolve-Path $p).Path
}
Ok "ComfyUI code: $($pick.Dir)"
Ok "Its Python:   $($pick.Python)"

# ------------------------------------------------------------------ 2. check it can run this
Step "Checking ComfyUI, graphics card and memory"

$nodes = Join-Path $pick.Dir 'comfy_extras\nodes_flux.py'
if (-not ((Test-Path $nodes) -and (Select-String -Path $nodes -Pattern 'FluxKontextImageScale' -Quiet))) {
    Fail "This ComfyUI is too old for FLUX Kontext. Update ComfyUI, then re-run setup."
}
Ok "ComfyUI version supports FLUX Kontext"

$check = @'
import json
r = {}
try:
    import torch
    r["torch"] = torch.__version__
    p = None
    if torch.cuda.is_available():  # NVIDIA (CUDA) and AMD (ROCm builds report through torch.cuda too)
        p = torch.cuda.get_device_properties(0)
        r["vendor"] = "AMD" if getattr(torch.version, "hip", None) else "NVIDIA"
    elif hasattr(torch, "xpu") and torch.xpu.is_available():
        p = torch.xpu.get_device_properties(0)
        r["vendor"] = "Intel"
    if p is not None:
        r["gpu"] = p.name; r["vram_gb"] = round(p.total_memory / 2**30, 1)
except Exception as e:
    r["torch_error"] = str(e)
for m in ("numpy", "PIL", "scipy"):
    try:
        __import__(m)
    except Exception as e:
        r.setdefault("missing", []).append(m)
print(json.dumps(r))
'@
$tmp = Join-Path $env:TEMP 'linecrafty_check.py'
Set-Content -Path $tmp -Value $check -Encoding ascii
$out = & $pick.Python -s $tmp 2>$null | Select-Object -Last 1
Remove-Item $tmp -ErrorAction SilentlyContinue
try { $r = $out | ConvertFrom-Json -ErrorAction Stop; if (-not $r) { throw 'empty' } } catch { Fail "ComfyUI's Python didn't run correctly. Is ComfyUI fully installed?" }
if ($r.torch_error) { Fail "PyTorch isn't working in ComfyUI's Python: $($r.torch_error)" }
if ($r.missing) { Fail "ComfyUI's Python is missing: $($r.missing -join ', '). Update/repair ComfyUI." }

$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
if ($r.gpu) {
    Ok "Graphics card: $($r.gpu) ($($r.vram_gb) GB)   RAM: $ramGB GB"
    if ($r.vram_gb -lt 7.5) { Warn "Under 8 GB of video memory: it may still work, just slower." }
    if ($r.vendor -eq 'AMD') { Info "AMD support is new. If pages fail or look wrong, please report it (comfy\server.log helps)." }
    if ($r.vendor -eq 'Intel') { Warn "Intel graphics are untested with Linecrafty. It may work; please report how it goes." }
} else {
    Warn "ComfyUI didn't find a supported graphics card (NVIDIA or AMD)."
    Warn "Without one, making a page would take a very long time (or fail)."
    Info "If you have an AMD card, make sure you installed ComfyUI's AMD version."
    if (-not (Ask "Continue anyway?" 'n')) { exit 1 }
}
if ($ramGB -lt 15) {
    Warn "Only $ramGB GB of RAM. 16 GB is the minimum and 32 GB is recommended; pages will probably fail."
    if (-not (Ask "Continue anyway?" 'n')) { exit 1 }
} elseif ($ramGB -lt 30) {
    Info "$ramGB GB of RAM: should work, but pages take longer. Close other programs while it runs (32 GB recommended)."
}

# ------------------------------------------------------------------ 3. SVG tracer
Step "Installing the SVG tracer (small, into this folder only)"
$lib = Join-Path $Root 'lib'
& $pick.Python -s -c "import sys; sys.path.insert(0, r'$lib'); import vtracer" 2>$null; $vtOk = $LASTEXITCODE -eq 0
if (-not $vtOk) {
    & $pick.Python -s -m pip install --target $lib --upgrade --disable-pip-version-check --no-warn-script-location -q vtracer 2>&1 | Out-Null
    & $pick.Python -s -c "import sys; sys.path.insert(0, r'$lib'); import vtracer" 2>$null; $vtOk = $LASTEXITCODE -eq 0
}
if ($vtOk) { Ok "vtracer ready" } else { Warn "Couldn't install vtracer; pages will be PNG only (no SVG)." }

# ------------------------------------------------------------------ 4. models
Step "Local AI models (they run on this PC; nothing is sent anywhere)"
$searchBases = @((Join-Path $Root 'models'), (Join-Path $pick.Dir 'models')) + $pick.Extra
$todo = @()
foreach ($m in $Models) {
    $found = $null
    foreach ($b in $searchBases) {
        $f = Join-Path $b "$($m.folder)\$($m.name)"
        if ((Test-Path $f) -and ((Get-Item $f).Length -eq $m.size)) { $found = $f; break }
    }
    if ($found) { Ok "have     $($m.name)" } else { Info "need     $($m.name)  ($(GB $m.size))"; $todo += $m }
}

if ($todo.Count) {
    $total = 0; foreach ($m in $todo) { $total += $m.size }   # (Measure-Object can't read hashtable keys in PS 5.1)
    $partial = 0
    foreach ($m in $todo) { $pp = Join-Path $Root "models\$($m.folder)\$($m.name).part"; if (Test-Path $pp) { $partial += (Get-Item $pp).Length } }
    $drive = (Get-Item $Root).PSDrive
    $free = (Get-PSDrive $drive.Name).Free
    Info ""
    Info "Download size: $(GB ($total - $partial)) into $Root\models  (free space on $($drive.Name): $(GB $free))"
    if ($free -lt ($total - $partial + 1GB)) { Fail "Not enough free disk space on drive $($drive.Name):." }
    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { Fail "curl.exe not found (needs Windows 10 or newer)." }
    Info "Note: FLUX.1 Kontext [dev] and 4x-AnimeSharp are licensed for NON-COMMERCIAL use only"
    Info "(see the License section of README.md). Pages you make are for personal use."
    if (-not (Ask "Download now and accept those licenses? (can take a while; safe to interrupt and re-run)")) { Fail "Models not downloaded. Re-run setup when ready." }

    foreach ($m in $todo) {
        $dir = Join-Path $Root "models\$($m.folder)"
        New-Item -ItemType Directory -Force $dir | Out-Null
        $dest = Join-Path $dir $m.name
        $part = "$dest.part"
        Write-Host ""
        Write-Host "   Downloading $($m.name) ($(GB $m.size))"
        $tries = 0
        while (-not ((Test-Path $part) -and ((Get-Item $part).Length -ge $m.size))) {
            $tries++
            if ($tries -gt 5) { Fail "Download of $($m.name) keeps failing. Check your internet and re-run setup (it resumes)." }
            & curl.exe -L --fail --retry 3 --retry-delay 5 -C - --progress-bar -o $part $m.url
            if ($LASTEXITCODE -ne 0) { Warn "Download interrupted (curl exit $LASTEXITCODE); retrying..."; Start-Sleep 5 }
        }
        Write-Host "   Verifying..." -NoNewline
        $hash = (Get-FileHash -Algorithm SHA256 $part).Hash.ToLower()
        if ($hash -ne $m.sha256) {
            Remove-Item $part -Force
            Write-Host ""
            Fail "$($m.name) was corrupted in download and has been deleted. Re-run setup to download it again."
        }
        Move-Item -Force $part $dest
        Write-Host " OK" -ForegroundColor Green
    }
}
Ok "All models present"

# ------------------------------------------------------------------ 5. save settings
Step "Saving settings"
$cfg = [ordered]@{ comfy_dir = $pick.Dir; python = $pick.Python; extra_model_dirs = @($pick.Extra) }
$utf8 = New-Object System.Text.UTF8Encoding $false
[IO.File]::WriteAllText((Join-Path $Root 'config.json'), ($cfg | ConvertTo-Json), $utf8)
[IO.File]::WriteAllText((Join-Path $Root 'python_path.txt'), $pick.Python, [Text.Encoding]::Default)
foreach ($d in 'input', 'output') { New-Item -ItemType Directory -Force (Join-Path $Root $d) | Out-Null }
Ok "Done"

Write-Host ""
Write-Host "  All set!" -ForegroundColor Green
Write-Host "  Drag one or more pictures onto 'Make Coloring Page.cmd' (in $Root)."
Write-Host "  Each page takes about 2-4 minutes. Finished pages appear in the 'output' folder."
Write-Host ""
