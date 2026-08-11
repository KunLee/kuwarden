<#
.SYNOPSIS
    Bring up every KuWarden component for local development.

.DESCRIPTION
    Six things have to be true before a run can start, and most of them fail late and
    confusingly if they are not: the tools exist, .env has a master key, the stack is up, the
    schema is migrated, a toolchain image is built, and kuwarden.yaml loads. This checks all
    of them up front and refuses with the fix, rather than starting processes that will fail
    at the first node several minutes later.

    The three long-running processes launch in their own windows deliberately. The worker's
    log is where everything interesting happens during a run, and a script that swallowed it
    into a file would hide exactly what you started the run to watch.

.PARAMETER SkipChecks
    Skip preflight. For a restart when you already know the environment is good.

.PARAMETER NoStart
    Run the checks and stop. Useful for finding out why a run refused.

.EXAMPLE
    pwsh -File scripts/dev-up.ps1

.EXAMPLE
    pwsh -File scripts/dev-up.ps1 -NoStart
#>
[CmdletBinding()]
param(
    [switch]$SkipChecks,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step { param($Text) Write-Host "`n=== $Text" -ForegroundColor Cyan }
function Write-Ok   { param($Text) Write-Host "  [ok] $Text" -ForegroundColor Green }
function Write-Warn { param($Text) Write-Host "  [--] $Text" -ForegroundColor Yellow }

function Stop-With {
    param($Problem, $Fix)
    Write-Host "  [!!] $Problem" -ForegroundColor Red
    Write-Host "`n  Fix:`n    $Fix`n" -ForegroundColor Yellow
    exit 1
}

# --- tools and secrets ---------------------------------------------------------------------

if (-not $SkipChecks) {
    Write-Step "Preflight"

    foreach ($tool in @("podman", "uv", "npm")) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            Stop-With "$tool is not on PATH" "Install $tool, then re-run."
        }
    }
    Write-Ok "podman, uv and npm are on PATH"

    if (-not (Test-Path ".env")) {
        Stop-With ".env is missing" "cp .env.example .env   # then set KUWARDEN_POSTGRES_PASSWORD"
    }
    # Existence only. Without this line every stored credential fails to decrypt, and the
    # failure surfaces per node, minutes into a run, as something that reads like a bug.
    if (-not (Select-String -Path ".env" -Pattern "^KUWARDEN_SECRET_KEY=" -Quiet)) {
        Stop-With "no KUWARDEN_SECRET_KEY in .env" `
            "uv run python -m engine.adapters.secrets keygen >> .env"
    }
    Write-Ok ".env has a master key"
}

# --- the stack -----------------------------------------------------------------------------

Write-Step "Temporal and PostgreSQL"
podman compose up -d --wait
if ($LASTEXITCODE -ne 0) { Stop-With "compose failed" "podman compose logs" }
Write-Ok "containers healthy"

Write-Step "Schema"
uv run python -m engine.db migrate
if ($LASTEXITCODE -ne 0) { Stop-With "migration failed" "Is PostgreSQL reachable? podman compose logs postgres" }

# --- toolchain image -----------------------------------------------------------------------

if (-not $SkipChecks) {
    Write-Step "Sandbox toolchain"
    # Read from the config rather than hardcoded, so a repository declaring a different image
    # is checked for *that* image instead of silently passing on the Python one.
    $image = uv run python -c "from engine.config import load; print(load('kuwarden.yaml').sandbox.toolchain_image)"
    if ($LASTEXITCODE -ne 0) {
        Stop-With "kuwarden.yaml does not load" "pwsh -File scripts/dev-up.ps1 -NoStart   # for the reason"
    }

    podman image exists $image
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "$image is not built - building (several minutes, once)"
        uv run python -m engine.sandbox build
        if ($LASTEXITCODE -ne 0) {
            Stop-With "toolchain build failed" `
                "Only bundled toolchains build this way. For another stack, add engine/sandbox/toolchains/<name>/Containerfile"
        }
    }
    Write-Ok "$image present"
}

# --- configuration and accounts ------------------------------------------------------------

uv run python scripts/preflight.py
if ($LASTEXITCODE -eq 1) { exit 1 }
if ($LASTEXITCODE -eq 2) { Stop-With "could not complete the checks" "See the error above." }

if ($NoStart) {
    Write-Host "`n  -NoStart: checks only, nothing launched.`n" -ForegroundColor Cyan
    exit 0
}

# --- the three processes -------------------------------------------------------------------

Write-Step "Starting worker, API and Workbench"

# Resolved here, in the parent, and passed to the children as absolute paths.
#
# A spawned window does not reliably see what this shell sees. `uv` installs to
# %USERPROFILE%\.local\bin and is added to the *User* PATH, so a terminal opened before that
# happened carries a stale PATH — and every child it spawns inherits the stale one. The
# symptom is three windows all reporting "'uv' is not recognized" while the parent script,
# which just used uv successfully, reports everything fine.
#
# Passing absolute paths *and* the parent's PATH removes the whole class of problem: `uv`
# itself shells out to python, so the resolved exe alone would not be enough.
$uv  = (Get-Command uv).Source
$npm = (Get-Command npm).Source
$childPath = $env:PATH -replace "'", "''"

function Start-Component {
    param($Title, $Command)
    # Its own window, and -NoExit so a crash leaves its traceback on screen instead of
    # closing over it.
    Start-Process pwsh -ArgumentList @(
        "-NoExit", "-Command",
        "`$env:PATH = '$childPath'; `$host.UI.RawUI.WindowTitle = '$Title'; " +
        "Set-Location '$root'; $Command"
    )
    Write-Ok $Title
}

Start-Component "kuwarden worker"    "& '$uv' run python -m engine.worker"
Start-Component "kuwarden api"       "& '$uv' run uvicorn engine.api.main:app --reload --port 8080"
Start-Component "kuwarden workbench" "Set-Location ui; & '$npm' run dev"

Write-Host @"

  Workbench   http://localhost:5173
  API docs    http://localhost:8080/docs
  Temporal    http://localhost:8233

  Wait for the worker window to print 'worker ready on ...'. Until that line appears it has
  not reached Temporal, and a started run will sit doing nothing.

  Stop everything:  pwsh -File scripts/dev-down.ps1

"@ -ForegroundColor Cyan
