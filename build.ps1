# Build script for NSOverlay
# Handles the MS Store Python DLL stub issue automatically.

Set-Location $PSScriptRoot

Write-Host "Building executable..." -ForegroundColor Cyan
& ".venv\Scripts\pyinstaller.exe" --windowed --onedir --name nsoverlay --icon icon.ico --noconfirm nsoverlay.py

Write-Host "Copying config.json..." -ForegroundColor Cyan
Copy-Item "config.json" "dist\nsoverlay\config.json" -Force

Write-Host "Patching python311.dll (MS Store Python fix)..." -ForegroundColor Cyan
Copy-Item "python311.dll" "dist\nsoverlay\_internal\python311.dll" -Force

Write-Host "Build complete! Executable at: dist\nsoverlay\nsoverlay.exe" -ForegroundColor Green
