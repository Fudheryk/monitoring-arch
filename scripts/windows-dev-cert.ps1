Write-Host "▶ Installing mkcert"

if (-not (Get-Command mkcert -ErrorAction SilentlyContinue)) {
    winget install FiloSottile.mkcert
}

Write-Host "▶ Installing mkcert CA into Windows trust store"
mkcert -install

Write-Host "✅ Windows trust store ready"
