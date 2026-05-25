#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$ENV_NAME = "juxt-build-test"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Push-Location $ROOT
try {
    Write-Host ""
    Write-Host "==> Creating conda environment '$ENV_NAME' (Python 3.12)..."
    conda create -y -n $ENV_NAME python=3.12 -c conda-forge

    Write-Host ""
    Write-Host "==> Installing package and PyInstaller..."
    conda run --no-capture-output -n $ENV_NAME pip install pyinstaller ".[ssh]"

    Write-Host ""
    Write-Host "==> Building with PyInstaller..."
    conda run --no-capture-output -n $ENV_NAME pyinstaller --noconfirm juxt.spec

    Write-Host ""
    Write-Host "==> Generating sample images..."
    conda run --no-capture-output -n $ENV_NAME python make_sample.py

    Write-Host ""
    Write-Host "==> Launching binary -- close the window when done."
    .\dist\juxt\juxt.exe sample_config.yaml
} finally {
    Write-Host ""
    Write-Host "==> Removing conda environment '$ENV_NAME'..."
    conda env remove -n $ENV_NAME -y
    Pop-Location
}
