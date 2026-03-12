# setup.ps1 - Windows PowerShell setup script

Write-Host "Creating virtual environment..."
python -m venv .venv

Write-Host "Activating virtual environment..."
if (Test-Path ".venv\Scripts\Activate.ps1") {
    .venv\Scripts\Activate.ps1
} elseif (Test-Path ".venv\bin\Activate.ps1") {
    .venv\bin\Activate.ps1
}

Write-Host "Installing dependencies..."
pip install -r requirements.txt

Write-Host "Installing Playwright Chromium..."
playwright install chromium

Write-Host "Setup complete!"
