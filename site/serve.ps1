# Serves this folder on http://localhost and opens the map in the browser.
#
# Why this exists: OpenStreetMap refuses tile requests without a Referer, and a
# page opened by double-clicking index.html (file://) sends none, so the
# background map cannot show that way. Over http://localhost it can.
#
# Uses only what ships with Windows (PowerShell 5.1 + .NET HttpListener): no
# Python, no install, no admin rights. Serves this folder only, read-only,
# to this machine only. Close the window to stop it.
#
# Kept ASCII-only on purpose: Windows PowerShell 5.1 reads a BOM-less script
# in the ANSI code page, and any Japanese text here would be garbled.

param(
    [int]$Port = 0,          # 0 = first free port from 8765
    [switch]$NoBrowser       # for tests: serve without opening a browser
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($PSScriptRoot)
if (-not $root.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
    $root += [System.IO.Path]::DirectorySeparatorChar
}

$mime = @{
    '.html' = 'text/html; charset=utf-8'
    '.js'   = 'text/javascript; charset=utf-8'
    '.css'  = 'text/css; charset=utf-8'
    '.json' = 'application/json; charset=utf-8'
    '.png'  = 'image/png'
    '.svg'  = 'image/svg+xml'
    '.ico'  = 'image/x-icon'
}

$candidates = if ($Port -gt 0) { @($Port) } else { 8765..8785 }
$listener = $null
foreach ($p in $candidates) {
    $l = New-Object System.Net.HttpListener
    $l.Prefixes.Add("http://localhost:$p/")
    try { $l.Start(); $listener = $l; break } catch { $l.Close() }
}
if (-not $listener) {
    Write-Host 'No free port found (tried 8765-8785).'
    exit 1
}

$url = $listener.Prefixes | Select-Object -First 1
Write-Host ''
Write-Host "  Map: $url"
Write-Host '  Close this window to stop the server.'
Write-Host ''
if (-not $NoBrowser) { Start-Process $url }

while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $res = $ctx.Response
    try {
        $rel = [Uri]::UnescapeDataString($ctx.Request.Url.AbsolutePath.TrimStart('/'))
        if ($rel -eq '' -or $rel.EndsWith('/')) { $rel += 'index.html' }
        $path = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($root, $rel))
        # Never serve anything outside this folder (e.g. /../../secret).
        if (-not $path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -or
            -not [System.IO.File]::Exists($path)) {
            $res.StatusCode = 404
        } else {
            $bytes = [System.IO.File]::ReadAllBytes($path)
            $ext = [System.IO.Path]::GetExtension($path).ToLowerInvariant()
            if ($mime.ContainsKey($ext)) { $res.ContentType = $mime[$ext] }
            else { $res.ContentType = 'application/octet-stream' }
            $res.ContentLength64 = $bytes.Length
            $res.OutputStream.Write($bytes, 0, $bytes.Length)
        }
    } catch {
        $res.StatusCode = 500
    } finally {
        $res.Close()
    }
}
