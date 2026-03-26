#!/usr/bin/env pwsh

$ErrorActionPreference = 'Stop'

$REPO_URL = if ($env:REPO_URL) { $env:REPO_URL } else { 'https://github.com/Bhanunamikaze/ai-dataset-generator.git' }
$SKILL_NAME = 'dataset-generator'
$TARGET = 'antigravity'
$TARGET_EXPLICIT = $false
$PROJECT_DIR = (Get-Location).Path
$PROJECT_DIR_EXPLICIT = $false
$FORCE = $false
$INSTALL_DEPS = $false
$ONLINE_MODE = $false
$SOURCE_MODE = 'auto'
$REPO_PATH = ''
$TEMP_DIR = $null

function Show-Usage {
@'
AI Dataset Generator Installer (Antigravity / Claude / Codex)

Usage:
  pwsh ./install.ps1 [options]

Options:
  --target <antigravity|claude|codex|global|all> Install target (default: antigravity)
  --project-dir <path>                            Project path for antigravity target (default: current directory)
  --skill-name <name>                             Installed folder name (default: dataset-generator)
  --repo-url <url>                                Source Git URL for remote mode
  --source <auto|local|remote>                    Source mode (default: auto)
  --repo-path <path>                              Use a specific local repository path as source
  --install-deps                                  Install optional Python dependencies
  --online                                        Fetch the latest release zip package instead of cloning
                                                  When no --target is supplied, defaults to --target all
  --force                                         Overwrite existing target directory
  -h, --help                                      Show help

Examples:
  pwsh ./install.ps1 --target antigravity --project-dir C:\path\to\project
  pwsh ./install.ps1 --target claude
  pwsh ./install.ps1 --target codex
  pwsh ./install.ps1 --target global
  pwsh ./install.ps1 --target all --project-dir C:\path\to\project
  pwsh ./install.ps1 --online

'@ | Write-Host
}

function Require-Cmd {
  param([Parameter(Mandatory = $true)][string]$Cmd)
  if (-not (Get-Command -Name $Cmd -ErrorAction SilentlyContinue)) {
    throw "Error: required command not found: $Cmd"
  }
}

function Resolve-Dir {
  param([Parameter(Mandatory = $true)][string]$Dir)
  if (-not (Test-Path -LiteralPath $Dir -PathType Container)) {
    throw "Error: directory not found: $Dir"
  }
  return (Resolve-Path -LiteralPath $Dir).Path
}

function Invoke-ExternalCommand {
  param(
    [Parameter(Mandatory = $true)][string]$Command,
    [string[]]$Arguments = @()
  )

  $stdoutPath = [System.IO.Path]::GetTempFileName()
  $stderrPath = [System.IO.Path]::GetTempFileName()

  try {
    $proc = Start-Process -FilePath $Command -ArgumentList $Arguments -Wait -PassThru `
      -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

    $stdout = Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue
    $stderr = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue

    if (-not [string]::IsNullOrEmpty($stdout)) {
      [Console]::Out.Write($stdout)
    }
    if (-not [string]::IsNullOrEmpty($stderr)) {
      [Console]::Out.Write($stderr)
    }

    return $proc.ExitCode
  }
  finally {
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
  }
}

function Copy-Skill {
  param(
    [Parameter(Mandatory = $true)][string]$Src,
    [Parameter(Mandatory = $true)][string]$Dest,
    [Parameter(Mandatory = $true)][string]$Label
  )

  if ((Test-Path -LiteralPath $Dest) -and (-not $FORCE)) {
    throw "Error: $Label target already exists: $Dest`nUse --force to overwrite."
  }

  $destParent = Split-Path -Path $Dest -Parent
  if (-not (Test-Path -LiteralPath $destParent)) {
    New-Item -ItemType Directory -Path $destParent -Force | Out-Null
  }

  if (Test-Path -LiteralPath $Dest) {
    Remove-Item -LiteralPath $Dest -Recurse -Force
  }

  New-Item -ItemType Directory -Path $Dest -Force | Out-Null

  $REQUIRED_PATHS = @("SKILL.md", "scripts", "sub-skills", "resources")
  foreach ($req in $REQUIRED_PATHS) {
    $srcPath = Join-Path $Src $req
    if (-not (Test-Path -LiteralPath $srcPath)) {
      throw "Error: required skill path not found: $srcPath"
    }
    $targetPath = Join-Path $Dest $req
    Copy-Item -LiteralPath $srcPath -Destination $targetPath -Recurse -Force
  }

  Get-ChildItem -Path $Dest -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  Get-ChildItem -Path $Dest -Recurse -File -Filter "*.pyc" | Remove-Item -Force -ErrorAction SilentlyContinue

  New-Item -ItemType Directory -Path (Join-Path $Dest "workspace") -Force | Out-Null

  Write-Host "Installed for ${Label}: $Dest"
}

function Get-WorkspaceRootForTool {
  param([Parameter(Mandatory = $true)][string]$Tool)

  switch ($Tool) {
    'antigravity' {
      if ($TARGET -eq 'antigravity') {
        return (Join-Path $PROJECT_DIR '.agent')
      }

      $agentRoot = Join-Path $PROJECT_DIR '.agent'
      if ($TARGET -eq 'all' -and ($PROJECT_DIR_EXPLICIT -or (Test-Path -LiteralPath $agentRoot -PathType Container))) {
        return $agentRoot
      }

      return $null
    }
    'claude' {
      $claudeRoot = Join-Path $PROJECT_DIR '.claude'
      if ($PROJECT_DIR_EXPLICIT -or (Test-Path -LiteralPath $claudeRoot -PathType Container)) {
        return $claudeRoot
      }
      return $null
    }
    'codex' {
      $codexRoot = Join-Path $PROJECT_DIR '.codex'
      if ($PROJECT_DIR_EXPLICIT -or (Test-Path -LiteralPath $codexRoot -PathType Container)) {
        return $codexRoot
      }
      return $null
    }
    default {
      throw "Error: unsupported install tool: $Tool"
    }
  }
}

function Get-GlobalRootForTool {
  param([Parameter(Mandatory = $true)][string]$Tool)

  switch ($Tool) {
    'antigravity' {
      return (Join-Path $HOME '.gemini/antigravity')
    }
    'claude' {
      if ($env:CLAUDE_HOME) { return $env:CLAUDE_HOME }
      return (Join-Path $HOME '.claude')
    }
    'codex' {
      if ($env:CODEX_HOME) { return $env:CODEX_HOME }
      return (Join-Path $HOME '.codex')
    }
    default {
      throw "Error: unsupported install tool: $Tool"
    }
  }
}

function Install-ToolAuto {
  param(
    [Parameter(Mandatory = $true)][string]$Src,
    [Parameter(Mandatory = $true)][string]$Tool,
    [Parameter(Mandatory = $true)][string]$SkillName
  )

  $installRoot = Get-WorkspaceRootForTool -Tool $Tool
  if ($installRoot) {
    $label = "${Tool}-local"
  }
  else {
    $installRoot = Get-GlobalRootForTool -Tool $Tool
    $label = "${Tool}-global"
  }

  $dest = Join-Path (Join-Path $installRoot 'skills') $SkillName
  Copy-Skill -Src $Src -Dest $dest -Label $label
}

function Install-ToolGlobal {
  param(
    [Parameter(Mandatory = $true)][string]$Src,
    [Parameter(Mandatory = $true)][string]$Tool,
    [Parameter(Mandatory = $true)][string]$SkillName
  )

  $installRoot = Get-GlobalRootForTool -Tool $Tool
  $dest = Join-Path (Join-Path $installRoot 'skills') $SkillName
  Copy-Skill -Src $Src -Dest $dest -Label "${Tool}-global"
}

$idx = 0
while ($idx -lt $args.Count) {
  $arg = $args[$idx]
  switch ($arg) {
    '--target' {
      if (($idx + 1) -ge $args.Count) { throw 'Error: missing value for --target' }
      $TARGET = $args[$idx + 1]
      $TARGET_EXPLICIT = $true
      $idx += 2
      continue
    }
    '--project-dir' {
      if (($idx + 1) -ge $args.Count) { throw 'Error: missing value for --project-dir' }
      $PROJECT_DIR = $args[$idx + 1]
      $PROJECT_DIR_EXPLICIT = $true
      $idx += 2
      continue
    }
    '--skill-name' {
      if (($idx + 1) -ge $args.Count) { throw 'Error: missing value for --skill-name' }
      $SKILL_NAME = $args[$idx + 1]
      $idx += 2
      continue
    }
    '--repo-url' {
      if (($idx + 1) -ge $args.Count) { throw 'Error: missing value for --repo-url' }
      $REPO_URL = $args[$idx + 1]
      $idx += 2
      continue
    }
    '--source' {
      if (($idx + 1) -ge $args.Count) { throw 'Error: missing value for --source' }
      $SOURCE_MODE = $args[$idx + 1]
      $idx += 2
      continue
    }
    '--repo-path' {
      if (($idx + 1) -ge $args.Count) { throw 'Error: missing value for --repo-path' }
      $REPO_PATH = $args[$idx + 1]
      $idx += 2
      continue
    }
    '--install-deps' {
      $INSTALL_DEPS = $true
      $idx += 1
      continue
    }
    '--online' {
      $ONLINE_MODE = $true
      $FORCE = $true
      $idx += 1
      continue
    }
    '--force' {
      $FORCE = $true
      $idx += 1
      continue
    }
    '-h' { Show-Usage; exit 0 }
    '--help' { Show-Usage; exit 0 }
    default {
      Show-Usage
      throw "Unknown option: $arg"
    }
  }
}

if ($TARGET -notin @('antigravity', 'claude', 'codex', 'global', 'all')) {
  throw "Error: invalid --target: $TARGET"
}

if ($SOURCE_MODE -notin @('auto', 'local', 'remote')) {
  throw "Error: invalid --source: $SOURCE_MODE"
}

if ($ONLINE_MODE -and (-not $TARGET_EXPLICIT)) {
  $TARGET = 'all'
}

Require-Cmd -Cmd 'python3'

$SCRIPT_DIR = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$SRC_DIR = ''
$SHOULD_CLONE = $false

if ($ONLINE_MODE) {
  Write-Host "Fetching latest release tag..."
  $zipUrl = ''
  try {
    $releaseInfo = Invoke-RestMethod -Uri "https://api.github.com/repos/Bhanunamikaze/ai-dataset-generator/releases/latest" -ErrorAction Stop
    $latestTag = $releaseInfo.tag_name
    if ([string]::IsNullOrWhiteSpace($latestTag)) { throw "Tag empty" }
    Write-Host "Downloading latest tag package: $latestTag"
    $zipUrl = "https://github.com/Bhanunamikaze/ai-dataset-generator/archive/refs/tags/${latestTag}.zip"
  }
  catch {
    Write-Host "Could not determine latest tag, falling back to main branch archive..."
    $zipUrl = "https://github.com/Bhanunamikaze/ai-dataset-generator/archive/refs/heads/main.zip"
  }

  $TEMP_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $TEMP_DIR -Force | Out-Null
  $zipPath = Join-Path $TEMP_DIR "package.zip"

  Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
  Expand-Archive -Path $zipPath -DestinationPath $TEMP_DIR -Force
  Remove-Item -Path $zipPath -Force

  $extractedDir = Get-ChildItem -Path $TEMP_DIR -Directory | Select-Object -First 1
  $SRC_DIR = $extractedDir.FullName
  Write-Host "Using downloaded package source: $SRC_DIR"
}
elseif (-not [string]::IsNullOrWhiteSpace($REPO_PATH)) {
  $SRC_DIR = Resolve-Dir -Dir $REPO_PATH
  Write-Host "Using repo path source: $SRC_DIR"
}
elseif ($SOURCE_MODE -eq 'local') {
  $SRC_DIR = $SCRIPT_DIR
  Write-Host "Using local source: $SRC_DIR"
}
elseif ($SOURCE_MODE -eq 'remote') {
  $SHOULD_CLONE = $true
}
elseif (Test-Path -LiteralPath (Join-Path $SCRIPT_DIR 'SKILL.md') -PathType Leaf) {
  $SRC_DIR = $SCRIPT_DIR
  Write-Host "Using local source: $SRC_DIR"
}
else {
  $SHOULD_CLONE = $true
}

try {
  if ($SHOULD_CLONE) {
    Require-Cmd -Cmd 'git'
    $TEMP_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $TEMP_DIR -Force | Out-Null

    $cloneDir = Join-Path $TEMP_DIR 'repo'
    Write-Host "Cloning source repo: $REPO_URL"

    $cloneExitCode = Invoke-ExternalCommand -Command 'git' -Arguments @('clone', '--depth', '1', $REPO_URL, $cloneDir)
    if ($cloneExitCode -ne 0) {
      throw "Error: failed to clone source repo: $REPO_URL`nTip: pass --repo-url <git-url> or set REPO_URL for remote installs."
    }

    $SRC_DIR = $cloneDir
    Write-Host "Using remote source: $SRC_DIR"
  }

  if (-not (Test-Path -LiteralPath (Join-Path $SRC_DIR 'SKILL.md') -PathType Leaf)) {
    throw "Error: SKILL.md not found in source directory: $SRC_DIR"
  }

  Write-Host ''
  Write-Host 'Installing AI Dataset Generator'
  Write-Host "Target: $TARGET"
  Write-Host "Skill name: $SKILL_NAME"
  Write-Host ''

  if ($TARGET -eq 'antigravity') {
    try {
      Install-ToolAuto -Src $SRC_DIR -Tool 'antigravity' -SkillName $SKILL_NAME
    } catch { Write-Warning "Skipped antigravity-local: $_" }
  }

  if ($TARGET -eq 'claude') {
    try {
      Install-ToolAuto -Src $SRC_DIR -Tool 'claude' -SkillName $SKILL_NAME
    } catch { Write-Warning "Skipped claude install: $_" }
  }

  if ($TARGET -eq 'codex') {
    try {
      Install-ToolAuto -Src $SRC_DIR -Tool 'codex' -SkillName $SKILL_NAME
    } catch { Write-Warning "Skipped codex install: $_" }
  }

  if ($TARGET -eq 'global') {
    try {
      Install-ToolGlobal -Src $SRC_DIR -Tool 'antigravity' -SkillName $SKILL_NAME
    } catch { Write-Warning "Skipped antigravity-global: $_" }
    try {
      Install-ToolGlobal -Src $SRC_DIR -Tool 'claude' -SkillName $SKILL_NAME
    } catch { Write-Warning "Skipped claude-global: $_" }
    try {
      Install-ToolGlobal -Src $SRC_DIR -Tool 'codex' -SkillName $SKILL_NAME
    } catch { Write-Warning "Skipped codex-global: $_" }
  }

  if ($TARGET -eq 'all') {
    try {
      Install-ToolAuto -Src $SRC_DIR -Tool 'antigravity' -SkillName $SKILL_NAME
    } catch { Write-Warning "Skipped antigravity install: $_" }
    try {
      Install-ToolAuto -Src $SRC_DIR -Tool 'claude' -SkillName $SKILL_NAME
    } catch { Write-Warning "Skipped claude install: $_" }
    try {
      Install-ToolAuto -Src $SRC_DIR -Tool 'codex' -SkillName $SKILL_NAME
    } catch { Write-Warning "Skipped codex install: $_" }
  }

  if ($INSTALL_DEPS) {
    Write-Host ''
    Write-Host 'Installing Python dependencies...'

    $reqPath = Join-Path $SRC_DIR 'requirements.txt'
    if (Test-Path -LiteralPath $reqPath) {
      $depsExitCode = Invoke-ExternalCommand -Command 'python3' -Arguments @('-m', 'pip', 'install', '--user', '-r', $reqPath)
      if ($depsExitCode -eq 0) {
        Write-Host 'Installed dependencies from requirements.txt'
      }
      else {
        Write-Warning 'Could not auto-install Python dependencies. Install manually:'
        Write-Host "  python3 -m pip install --user -r $reqPath"
      }
    }
    else {
        $depsExitCode = Invoke-ExternalCommand -Command 'python3' -Arguments @('-m', 'pip', 'install', '--user', 'jsonschema')
        if ($depsExitCode -eq 0) {
          Write-Host 'Installed fallback dependency: jsonschema'
        }
    }
  }

  Write-Host ''
  Write-Host 'Install complete.'
  Write-Host 'Next: restart your tool session to pick up the installed skill.'
}
finally {
  if ($TEMP_DIR -and (Test-Path -LiteralPath $TEMP_DIR)) {
    Remove-Item -LiteralPath $TEMP_DIR -Recurse -Force
  }
}
