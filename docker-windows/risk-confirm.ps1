# Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Show RISK-NOTICE.txt and store a local acceptance. No network call.
# The box starts empty. A console accepts only "I UNDERSTAND" or "ICH VERSTEHE".

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Notice = Join-Path $Here "RISK-NOTICE.txt"
$Dest = Join-Path $env:USERPROFILE ".config\rimbaud\risk-notice.json"

if (-not (Test-Path -LiteralPath $Notice)) {
  Write-Error "missing $Notice"
  exit 1
}

function Get-NoticeHash([string]$Path) {
  $cert = & certutil -hashfile $Path SHA256
  foreach ($line in $cert) {
    $t = "$line".Trim()
    if ($t -match '^[0-9A-Fa-f]{64}$') {
      return $t.ToLower()
    }
  }
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
}

function Get-RimbaudVersion([string]$Start) {
  $d = Get-Item -LiteralPath $Start
  while ($null -ne $d) {
    $archy = Join-Path $d.FullName "conf\archy.json"
    if (Test-Path -LiteralPath $archy) {
      try {
        $raw = [System.IO.File]::ReadAllText($archy, (New-Object System.Text.UTF8Encoding $false))
        $j = $raw | ConvertFrom-Json
        if ($j.version) { return [string]$j.version }
      } catch {}
    }
    $ver = Join-Path $d.FullName "VERSION"
    if (Test-Path -LiteralPath $ver) {
      $line = [System.IO.File]::ReadAllLines($ver, (New-Object System.Text.UTF8Encoding $false)) | Select-Object -First 1
      if ($line) { return "$line".Trim() }
    }
    $d = $d.Parent
  }
  return "unknown"
}

$utf8 = New-Object System.Text.UTF8Encoding $false
$text = [System.IO.File]::ReadAllText($Notice, $utf8)
$hash = Get-NoticeHash $Notice

if (Test-Path -LiteralPath $Dest) {
  try {
    $saved = [System.IO.File]::ReadAllText($Dest, $utf8) | ConvertFrom-Json
    if ($saved.notice_sha256 -eq $hash -and $saved.notice -eq $text) {
      Write-Output "Risk notice already accepted on this computer."
      exit 0
    }
  } catch {}
}

function Save-Acceptance {
  $obj = [ordered]@{
    accepted_at   = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    version       = Get-RimbaudVersion $Here
    notice_sha256 = $hash
    notice        = $text
  }
  $dir = Split-Path -Parent $Dest
  if (-not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }
  $json = ($obj | ConvertTo-Json -Compress)
  [System.IO.File]::WriteAllText($Dest, $json + "`n", $utf8)
  Write-Output "Saved the risk notice at $Dest"
}

function Show-RiskDialog {
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  $form = New-Object System.Windows.Forms.Form
  $form.Text = "rimBaud risk notice"
  $form.StartPosition = "CenterScreen"
  $form.Width = 760
  $form.Height = 560
  $form.MinimizeBox = $false
  $form.MaximizeBox = $false
  $box = New-Object System.Windows.Forms.TextBox
  $box.Multiline = $true
  $box.ReadOnly = $true
  $box.ScrollBars = "Vertical"
  $box.Font = New-Object System.Drawing.Font("Segoe UI", 10)
  $box.Dock = "Fill"
  $box.Text = $text
  $check = New-Object System.Windows.Forms.CheckBox
  $check.Text = "I understand. Only money I can afford to lose belongs in the account. / Ich verstehe. Auf das Konto gehört nur Geld, dessen Verlust verkraftbar ist."
  $check.AutoSize = $false
  $check.Height = 48
  $check.Dock = "Bottom"
  $check.Checked = $false
  $buttons = New-Object System.Windows.Forms.FlowLayoutPanel
  $buttons.Height = 40
  $buttons.Dock = "Bottom"
  $buttons.FlowDirection = "RightToLeft"
  $ok = New-Object System.Windows.Forms.Button
  $ok.Text = "Continue"
  $ok.Width = 120
  $ok.Enabled = $false
  $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
  $cancel = New-Object System.Windows.Forms.Button
  $cancel.Text = "Cancel"
  $cancel.Width = 120
  $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
  $script:RiskOk = $ok
  $script:RiskCheck = $check
  $check.Add_CheckedChanged({ $script:RiskOk.Enabled = $script:RiskCheck.Checked })
  [void]$buttons.Controls.Add($ok)
  [void]$buttons.Controls.Add($cancel)
  $form.Controls.Add($box)
  $form.Controls.Add($check)
  $form.Controls.Add($buttons)
  $form.AcceptButton = $ok
  $form.CancelButton = $cancel
  $result = $form.ShowDialog()
  return ($result -eq [System.Windows.Forms.DialogResult]::OK -and $check.Checked)
}

$gui = $false
try {
  Add-Type -AssemblyName System.Windows.Forms
  $gui = $true
} catch {
  $gui = $false
}

if ($gui -and [Environment]::UserInteractive) {
  $ok = Show-RiskDialog
  if (-not $ok) { exit 1 }
} else {
  Write-Output $text
  Write-Output ""
  Write-Output "Type I UNDERSTAND or ICH VERSTEHE to continue."
  $line = Read-Host
  if ($line -ne "I UNDERSTAND" -and $line -ne "ICH VERSTEHE") { exit 1 }
}

Save-Acceptance
exit 0
