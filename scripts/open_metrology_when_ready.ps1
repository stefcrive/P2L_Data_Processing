param([int]$Port)
$url = "http://127.0.0.1:$Port/metrology"
for ($attempt = 0; $attempt -lt 180; $attempt++) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2
        if ($response.StatusCode -eq 200) { Start-Process $url; exit 0 }
    } catch { }
    Start-Sleep -Milliseconds 500
}
