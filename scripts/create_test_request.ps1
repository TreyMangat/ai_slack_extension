# Create a test feature request via the JSON API
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\create_test_request.ps1

$body = @{
  spec = @{
    title = "Export invoices as CSV"
    problem = "Finance needs a CSV export to reconcile invoices"
    business_justification = "Reduces manual reconciliation effort and shortens month-end close."
    implementation_mode = "reuse_existing"
    source_repos = @(
      "/app/app/samples/reuse_seed"
    )
    proposed_solution = "Add an Export button on /invoices that downloads a CSV"
    acceptance_criteria = @(
      "User can click Export on the invoices page",
      "CSV downloads with columns: invoice_id, customer, amount, status",
      "Export respects current filters"
    )
    non_goals = @(
      "No redesign",
      "No changes to billing logic"
    )
    repo = ""
    risk_flags = @()
    links = @()
  }
  requester_user_id = "local-user"
}

$json = $body | ConvertTo-Json -Depth 6

Write-Host "Creating request..." -ForegroundColor Cyan
$headers = @{}
if (Test-Path ".env") {
  $tokenLine = Select-String -Path ".env" -Pattern '^API_AUTH_TOKEN=' -ErrorAction SilentlyContinue
  if ($tokenLine) {
    $token = (($tokenLine.Line -split '=', 2)[1]).Trim()
    if ($token) {
      $headers["X-FF-Token"] = $token
    }
  }
}

if ($headers.Count -gt 0) {
  $response = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/feature-requests" -Body $json -ContentType "application/json" -Headers $headers
} else {
  $response = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/feature-requests" -Body $json -ContentType "application/json"
}
$response | ConvertTo-Json -Depth 6

Write-Host "Open: http://localhost:8000/features/$($response.id)" -ForegroundColor Green
