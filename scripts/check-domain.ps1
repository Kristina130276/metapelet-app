# Проверка: открывает ли www.metapelet.org приложение MetaPelet
$urls = @(
    "https://www.metapelet.org/",
    "https://metapelet-bot-flask.onrender.com/"
)

foreach ($url in $urls) {
    Write-Host "`n=== $url ===" -ForegroundColor Cyan
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 60
        $ok = $resp.Content -match "landing-screen" -and $resp.Content -match "Поговорить"
        if ($ok) {
            Write-Host "OK: MetaPelet app detected" -ForegroundColor Green
        } else {
            Write-Host "WARN: page loaded but app markers not found (maybe Canva?)" -ForegroundColor Yellow
            Write-Host ($resp.Content.Substring(0, [Math]::Min(200, $resp.Content.Length)))
        }
    } catch {
        Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host "`nDNS www.metapelet.org:" -ForegroundColor Cyan
nslookup www.metapelet.org 2>$null | Select-String "Name|Address|Aliases|canva|onrender"
