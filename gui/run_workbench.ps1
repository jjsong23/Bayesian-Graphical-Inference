param(
    [int]$Port = 8765
)

$workbenchDir = $PSScriptRoot
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCommand) {
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
}
if ($null -eq $pythonCommand) {
    throw "Python 3 was not found. Start the workbench from the Codex project environment."
}

& $pythonCommand.Source "$workbenchDir\server.py" --port $Port
