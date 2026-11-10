$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
function Get-TaskHResult($exception) {
    while ($exception.InnerException) { $exception = $exception.InnerException }
    return $exception.HResult
}
$phase = 'input'
$failure = 'TASK_OPERATION_FAILED'
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $phase = 'service'
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $service.GetFolder([string][char]92)
    $phase = 'task'
    $task = $null
    try { $task = $folder.GetTask($request.name) } catch {
        if ((Get-TaskHResult $_.Exception) -ne -2147024894) { throw }
    }
    if ($task) {
        [xml]$existing = $task.Xml
        $ns = [System.Xml.XmlNamespaceManager]::new($existing.NameTable)
        $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
        $source = $existing.SelectSingleNode('/t:Task/t:RegistrationInfo/t:Source', $ns)
        $action = $existing.SelectSingleNode('/t:Task/t:Actions/t:Exec', $ns)
        if (-not $source -or $source.InnerText -cne $request.source -or -not $action -or
            $action.Command -cne $request.command -or $action.Arguments -cne $request.arguments -or
            $action.WorkingDirectory -cne $request.directory) {
            $failure = 'TASK_OWNERSHIP_CONFLICT'
            throw $failure
        }
    }
    switch ($request.operation) {
        'query' { }
        'register' {
            $flags = if ($task) { 4 } else { 2 }
            $task = $folder.RegisterTask($request.name, $request.xml, $flags, $sid, $null, 3, $null)
        }
        'disable' { if ($task) { $task.Enabled = $false } }
        'remove' {
            if ($task) {
                if ($task.State -eq 4) {
                    $failure = 'TASK_RUNNING'
                    throw $failure
                }
                $folder.DeleteTask($request.name, 0)
                $task = $null
            }
        }
        'run' {
            if (-not $task -or -not $task.Enabled) {
                $failure = 'TASK_NOT_ENABLED'
                throw $failure
            }
            $null = $task.Run($null)
        }
        default { throw 'INVALID_OPERATION' }
    }
    $result = @{ok=$true; sid=$sid; installed=[bool]$task}
    if ($task) {
        $result.xml = $task.Xml
        $result.enabled = [bool]$task.Enabled
        $result.state = [int]$task.State
        $result.last_result = [int64]$task.LastTaskResult
        $result.last_run = $task.LastRunTime.ToString('o')
        $result.next_run = $task.NextRunTime.ToString('o')
        $result.trigger_users = @($task.Definition.Triggers | Where-Object { $_.Type -eq 9 -or $_.Type -eq 11 } | ForEach-Object {
            $account = [string]$_.UserId
            if ($account.StartsWith('S-1-')) { $account } else {
                ([System.Security.Principal.NTAccount]::new($account)).Translate([System.Security.Principal.SecurityIdentifier]).Value
            }
        })
    }
    $result | ConvertTo-Json -Compress -Depth 5
} catch {
    @{ok=$false; error=$failure; phase=$phase; hresult=(Get-TaskHResult $_.Exception)} | ConvertTo-Json -Compress
    exit 1
}
