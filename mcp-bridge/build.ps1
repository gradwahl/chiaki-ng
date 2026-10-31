# Build a separate console MCP server, without modifying Chiaki-ng's Qt GUI.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $PSScriptRoot "dist\chiaki-screen-mcp.exe"
Push-Location $repo
try {
    & python -m unittest discover -s mcp-bridge/tests -v
    if ($LASTEXITCODE -ne 0) { throw "MCP unit tests failed" }
    & python -m PyInstaller --noconfirm --clean --onefile --console --name chiaki-screen-mcp --distpath (Join-Path $PSScriptRoot "dist") --workpath (Join-Path $PSScriptRoot "build") --specpath $PSScriptRoot (Join-Path $PSScriptRoot "chiaki_screen_mcp.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
    & $exe --self-test
    if ($LASTEXITCODE -ne 0) { throw "Built executable self-test failed" }
    & python (Join-Path $PSScriptRoot "tests\smoke_exe.py") $exe
    if ($LASTEXITCODE -ne 0) { throw "Built executable MCP smoke test failed" }
    Write-Host "PASS: built and tested $exe"
}
finally { Pop-Location }
