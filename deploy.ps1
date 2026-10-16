<#
.SYNOPSIS
    Publishes the Finan folder to a GitHub repository: code only.

.DESCRIPTION
    This script commits and pushes. It publishes NOTHING to the web.

    The Pages source of this project is GitHub Actions, so the public site only appears
    after a workflow run uploads the site/ artifact. Pushing moves the files into the
    repository; the run on the Actions tab is what puts them on the public URL.

    Run it from the Finan folder:
        powershell -ExecutionPolicy Bypass -File .\deploy.ps1 -RemoteUrl https://github.com/<you>/finan

.NOTES
    .gitignore and .gitattributes are hand-written committed deliverables. This script only
    VERIFIES them; it never writes them. A script that generated the file protecting the
    secrets would mean the protection exists only after someone remembers to run it.
#>

param(
  [Parameter(Mandatory = $true)][string]$RemoteUrl,
  [string]$Branch = "main",
  [string]$CommitMessage = "Finan: index tracker, PWA and daily job",
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
# PowerShell 7.3+ turns a non-zero exit code from a native command into a terminating error when
# $ErrorActionPreference is 'Stop'. Several git calls below are expected to fail (an unset
# config value, an older git without `init -b`), so success is judged by $LASTEXITCODE instead.
# The variable does not exist on PowerShell 5.1, where assigning it is harmless.
$PSNativeCommandUseErrorActionPreference = $false

# Every path this script prints is relative: the repository is public and nothing published
# may name the PC or the user account.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------------------------------------------------------------- 1. git on PATH
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host "git was not found on PATH."
  Write-Host "Install Git for Windows from https://git-scm.com/download/win, reopen PowerShell"
  Write-Host "and run this script again."
  exit 1
}

# ---------------------------------------------------------------- 2. git identity
$identityEmail = git config user.email 2>$null
$identityName  = git config user.name 2>$null
if ([string]::IsNullOrWhiteSpace($identityEmail) -or [string]::IsNullOrWhiteSpace($identityName)) {
  Write-Host "git has no name/email configured, so it cannot create a commit."
  Write-Host "Run these two commands once (this script never writes git config itself):"
  Write-Host ""
  Write-Host '  git config --global user.name "Your Name"'
  Write-Host '  git config --global user.email "you@example.com"'
  Write-Host ""
  Write-Host "then run this script again."
  exit 1
}

# ---------------------------------------------------------------- 3. remote URL -> owner/repo
$pattern = '^(https://github\.com/[^/]+/[^/]+?(\.git)?|git@github\.com:[^/]+/[^/]+?(\.git)?)$'
if ($RemoteUrl -notmatch $pattern) {
  Write-Host "-RemoteUrl does not look like a GitHub repository URL:"
  Write-Host "  $RemoteUrl"
  Write-Host "Use one of these shapes:"
  Write-Host "  https://github.com/<you>/finan"
  Write-Host "  git@github.com:<you>/finan.git"
  exit 1
}
$trimmed = $RemoteUrl -replace '\.git$', ''
if ($trimmed -like 'git@*') { $ownerRepo = ($trimmed -split ':', 2)[1] }
else                        { $ownerRepo = $trimmed -replace '^https://github\.com/', '' }
$parts = $ownerRepo -split '/'
$owner = $parts[0]
$repo  = $parts[1]

# ---------------------------------------------------------------- 4. verify the two git files
$requiredIgnore = @(
  '__pycache__/', '*.py[cod]', '.venv/', 'venv/', 'env/', 'data/', 'logs/', 'report.html',
  'secrets/', '*vapid*private*', 'site/*.tmp', '.agents/', '.DS_Store', 'Thumbs.db'
)
$ignorePath = Join-Path $root '.gitignore'
$attrPath   = Join-Path $root '.gitattributes'

if (-not (Test-Path -LiteralPath $ignorePath)) {
  Write-Host ".gitignore is missing. It is a hand-written file this script only checks, never writes."
  Write-Host "Restore it with these lines, one per line:"
  $requiredIgnore | ForEach-Object { Write-Host "  $_" }
  exit 1
}
$ignoreLines = @(Get-Content -LiteralPath $ignorePath | ForEach-Object { $_.Trim() })
$missing = @($requiredIgnore | Where-Object { $ignoreLines -notcontains $_ })
if ($missing.Count -gt 0) {
  Write-Host ".gitignore is missing entries that keep secrets and generated files out of the repo."
  Write-Host "Add these lines to .gitignore, one per line:"
  $missing | ForEach-Object { Write-Host "  $_" }
  exit 1
}
if (-not (Test-Path -LiteralPath $attrPath)) {
  Write-Host ".gitattributes is missing. Create it with this single line:"
  Write-Host "  site/data.json text eol=lf"
  exit 1
}
$attrLines = @(Get-Content -LiteralPath $attrPath | ForEach-Object { $_.Trim() })
if ($attrLines -notcontains 'site/data.json text eol=lf') {
  Write-Host ".gitattributes does not contain the required line. Add it:"
  Write-Host "  site/data.json text eol=lf"
  exit 1
}
Write-Host "Checked .gitignore and .gitattributes."

# ---------------------------------------------------------------- 5. init only when absent
if (-not (Test-Path -LiteralPath (Join-Path $root '.git'))) {
  Write-Host "Creating a new git repository on branch $Branch."
  git -C $root init -b $Branch | Out-Null
  if ($LASTEXITCODE -ne 0) {
    # git older than 2.28 has no -b
    git -C $root init | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "git init failed."; exit 1 }
    git -C $root branch -M $Branch | Out-Null
  }
} else {
  Write-Host "Using the git repository that is already here."
}

# ---------------------------------------------------------------- 6. stage an explicit allow-list
# Never `git add -A`: that would stage whatever else happens to be in the folder.
$allowList = @(
  'tracker.py', 'config.json', 'requirements.txt', 'requirements-windows.txt',
  'register_task.ps1', 'unregister_task.ps1', 'deploy.ps1',
  'README.md', 'README_IPHONE.md', '.gitignore', '.gitattributes',
  'finan', 'jobs', 'scripts', 'site', '.github'
)
foreach ($item in $allowList) {
  if (Test-Path -LiteralPath (Join-Path $root $item)) {
    git -C $root add -- $item
    if ($LASTEXITCODE -ne 0) { Write-Host "git add failed for $item"; exit 1 }
  } else {
    Write-Host "note: $item is not here, skipping it."
  }
}

# ---------------------------------------------------------------- 7. forbidden-path filter
$staged = @(git -C $root diff --cached --name-only)

# --- forbidden-path filter (begin) --- keep this block literal; the acceptance check evaluates
# it standalone against a sample list, so it must depend on nothing but $staged.
$forbidden = $staged | Where-Object {
  $_ -match '^secrets/' -or $_ -match 'vapid[_-]?private' -or $_ -match '(^|/)\.env$' -or
  $_ -match '^data/' -or $_ -match '^logs/' -or $_ -eq 'report.html'
}
# --- forbidden-path filter (end) ---

if ($forbidden) {
  Write-Host "Refusing to commit: these staged paths look like secrets or local-only files:"
  $forbidden | ForEach-Object { Write-Host "  $_" }
  Write-Host "Unstage them (git reset) and check .gitignore before trying again."
  exit 1
}

# ---------------------------------------------------------------- 8. show and confirm
Write-Host ""
Write-Host "Remote:  $RemoteUrl"
Write-Host "Branch:  $Branch"
Write-Host "Staged paths ($($staged.Count)):"
$staged | ForEach-Object { Write-Host "  $_" }
Write-Host ""
if (-not $Force) {
  $answer = Read-Host "Push these to GitHub? [y/N]"
  if ($answer -notmatch '^(y|yes)$') { Write-Host "Nothing was pushed."; exit 0 }
}

# ---------------------------------------------------------------- 9. remote, commit, push
$existing = @(git -C $root remote)
if ($existing -contains 'origin') { git -C $root remote set-url origin $RemoteUrl }
else                              { git -C $root remote add origin $RemoteUrl }
if ($LASTEXITCODE -ne 0) { Write-Host "could not set the origin remote."; exit 1 }

if ($staged.Count -gt 0) {
  git -C $root commit -m $CommitMessage
  if ($LASTEXITCODE -ne 0) { Write-Host "git commit failed."; exit 1 }
} else {
  Write-Host "Nothing new to commit - pushing whatever is not on GitHub yet."
}

git -C $root push -u origin $Branch
if ($LASTEXITCODE -ne 0) {
  Write-Host "git push failed. Create the repository on github.com first (see step 1 below),"
  Write-Host "then run this script again."
  exit 1
}

# ---------------------------------------------------------------- 10. the follow-up links
Write-Host ""
Write-Host "Pushed. This published NOTHING on the web yet - the site is deployed by a workflow run."
Write-Host ""
Write-Host "Next steps, in this order:"
Write-Host "  1. The repository must already exist (there is no gh CLI here, so it is created by hand):"
Write-Host "     https://github.com/new"
Write-Host "  2. Pages:   https://github.com/$owner/$repo/settings/pages"
Write-Host "     set Source to: GitHub Actions   (not a branch)"
Write-Host "  3. Secrets: https://github.com/$owner/$repo/settings/secrets/actions"
Write-Host "     VAPID_PRIVATE_KEY, PUSH_SUBSCRIPTIONS (and NTFY_TOPIC if you use ntfy)"
Write-Host "  4. Actions: https://github.com/$owner/$repo/actions"
Write-Host "     open 'Finan daily check' and click Run workflow - this run is what publishes site/"
Write-Host "  5. Then the app is at: https://$owner.github.io/$repo/"
Write-Host "     open that in Safari on the iPhone and use Share > Add to Home Screen."
Write-Host ""
Write-Host "The full walkthrough is in README_IPHONE.md."
exit 0
