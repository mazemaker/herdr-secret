# herdr-secret installer for Windows: installs the herdr plugin, then runs its setup.
#   irm https://raw.githubusercontent.com/mazemaker/herdr-secret/main/install.ps1 | iex
$ErrorActionPreference = "Stop"
$Repo = "mazemaker/herdr-secret"
$Id = "mazemaker.secret"

if (-not (Get-Command herdr -ErrorAction SilentlyContinue)) { throw "herdr is required: https://herdr.dev" }
$Py = if (Get-Command py -ErrorAction SilentlyContinue) { @("py", "-3") }
      elseif (Get-Command python -ErrorAction SilentlyContinue) { @("python") }
      else { throw "Python 3.8 or newer is required (python.org installer)" }

function Get-PluginRoot {
  $list = herdr plugin list --json | ConvertFrom-Json
  ($list.result.plugins | Where-Object { $_.plugin_id -eq $Id }).plugin_root
}

if (Get-PluginRoot) {
  Write-Host "herdr-secret is already installed; updating it."
  herdr plugin update $Id
} else {
  herdr plugin install $Repo
}
$Root = Get-PluginRoot
if (-not $Root) { throw "herdr did not report the plugin folder; run: herdr plugin action invoke $Id.setup-windows" }
$Exe = $Py[0]; $PyArgs = @($Py | Select-Object -Skip 1) + @("-I", (Join-Path $Root "bin\herdr_secret.py"), "setup")
& $Exe @PyArgs
