# Neverlank app: switch on the automatic tests and remove stale duplicate files.
# Stops at the first problem and says what to do. Nothing is pushed unless every check passes.

$ErrorActionPreference = "Stop"
$repo = "C:\Users\aster\OneDrive\Neverlank APP Documentation\Neverlank-Chartered-Accountants-App"

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Stop-Here($text) { Write-Host ""; Write-Host "STOPPED: $text" -ForegroundColor Red; exit 1 }
function Git-Run {
    & git @args
    if ($LASTEXITCODE -ne 0) { Stop-Here "git $($args -join ' ') failed. Nothing further was done. Send this window's text to Claude." }
}

Set-Location $repo

Step "1. Checking git and python are available"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Stop-Here "git was not found." }
$py = $null
foreach ($c in @("python", "py")) { if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break } }
if (-not $py) { Stop-Here "python was not found." }
Write-Host "git and $py found."

Step "2. Checking nothing else is waiting to be committed"
& git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { Stop-Here "Some changes are already staged. Commit or unstage them first (git status shows them)." }
Write-Host "Nothing staged."

Step "3. Checking the app uses the folders (packages), not the old single files"
foreach ($m in @("models", "engagements", "tax", "workpapers", "financials")) {
    $origin = & $py -c "import importlib.util as u; s=u.find_spec('$m'); print(s.origin if s else 'MISSING')"
    Write-Host ("{0,-12} {1}" -f $m, $origin)
    if ($origin -notmatch "[\\/]$m[\\/]__init__\.py$") { Stop-Here "$m is not loaded from its folder ($origin). Old file NOT removed. Send this window's text to Claude." }
}
Write-Host "All five load from their folders. The old single files are safe to remove."

Step "4. Putting the test workflow in place"
if (-not (Test-Path "Claude outputs\tests.yml")) { Stop-Here "Claude outputs\tests.yml is missing." }
New-Item -ItemType Directory -Force ".github\workflows" | Out-Null
Copy-Item "Claude outputs\tests.yml" ".github\workflows\tests.yml" -Force
Write-Host "Copied to .github\workflows\tests.yml"

Step "5. Removing stale duplicate files from git"
Git-Run rm --ignore-unmatch models.py engagements.py tax.py workpapers.py financials.py
Git-Run rm --ignore-unmatch app-1.py app-2.py app-3.py models-1.py models-2.py corporate_finance_ma-1.py firm_finance-1.py cf_ma_import-1.py workpapers-1.py templates/corpfin/ma_import-1.html
Git-Run rm -r --cached --ignore-unmatch "Claude outputs"
Add-Content -Path .gitignore -Value "`nClaude outputs/"

Step "6. Committing"
Git-Run add .github .gitignore
Git-Run commit -m "Switch on automatic tests and remove stale duplicate files" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01Dq9HgqbdeP7FfLVNZ4DSAc"

Step "7. Pushing to GitHub (Render redeploys after this)"
& git push
if ($LASTEXITCODE -ne 0) { Stop-Here "The push was refused. Run 'git pull --rebase' and then 'git push', or send this window's text to Claude." }

Write-Host ""
Write-Host "DONE. Now open the Actions tab on GitHub: the Tests workflow should start within a minute." -ForegroundColor Green
