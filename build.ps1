# build.ps1 — Wrapper for docker compose build
# Usage: .\build.ps1 [compose args]

$baseImage = "python:3.11-slim@sha256:e031123e3d85762b141ad1cbc56452ba69c6e722ebf2f042cc0dc86c47c0d8b3"

# Step 1: Pre-pull base image (daemon transparent proxy works)
Write-Host "[1/3] Pre-pulling base image..." -ForegroundColor Cyan
docker pull python:3.11-slim 2>$null

# Step 2: Warm BuildKit metadata cache (CLI uses Windows proxy)
Write-Host "[2/3] Warming BuildKit metadata cache..." -ForegroundColor Cyan
docker buildx imagetools inspect $baseImage 2>$null

# Step 3: Build & run
Write-Host "[3/3] Building & starting..." -ForegroundColor Cyan
docker compose up --build @args
