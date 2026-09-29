param()

$ErrorActionPreference = 'Stop'
$securityModule = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
Import-Module -Name $securityModule -ErrorAction Stop
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtimeDir = Join-Path $projectRoot '.runtime'
$python = Join-Path $runtimeDir 'worker-venv\Scripts\python.exe'
$keyFile = Join-Path $runtimeDir 'deepseek-api-key.dpapi'

if (-not (Test-Path -LiteralPath $python)) {
    throw '缺少项目固定 Python 环境，请按 README 安装依赖。'
}

function Test-LocalPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $result = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($result)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Set-WorkerKey([string]$CredentialPath) {
    if (-not [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) { return }
    if (Test-Path -LiteralPath $CredentialPath) {
        try {
            $secure = (Get-Content -LiteralPath $CredentialPath -Raw).Trim() | ConvertTo-SecureString
        } catch {
            throw "无法读取此 Windows 账号保存的密钥：$CredentialPath（$($_.Exception.GetType().Name)：$($_.Exception.Message)）。请删除该加密文件后重新启动。"
        }
    } else {
        Write-Host '首次启动接单服务，请输入 DeepSeek API Key（输入内容不回显）：'
        $secure = Read-Host -AsSecureString
        if ($secure.Length -eq 0) { throw '未输入 API Key；后台接单服务未启动。' }
        $secure | ConvertFrom-SecureString | Set-Content -LiteralPath $CredentialPath -Encoding UTF8
        Write-Host '密钥已使用当前 Windows 账号加密保存，后续双击无需重输。'
    }
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $env:DEEPSEEK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

Push-Location $projectRoot
try {
    $workerOnline = (& $python -c "from server.agent_queue import agent_queue; print('1' if agent_queue.get_worker_status()['online'] else '0')") -eq '1'
    $workerProcesses = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'python.exe' -and
        $_.CommandLine -like "*$python*" -and
        $_.CommandLine -like '*-m server.agent_worker*'
    })
    if ($workerProcesses.Count -gt 0) {
        if ($workerOnline) {
            Write-Host '后台接单服务已在线，不重复启动。'
        } else {
            Write-Warning '检测到本项目接单进程，但心跳已过期；请查看 worker 日志。为避免重复接单，没有启动第二个进程。'
        }
    } else {
        Set-WorkerKey -CredentialPath $keyFile
        try {
            $workerArgs = @('-m', 'server.agent_worker')
            if (-not [string]::IsNullOrWhiteSpace($env:HARVEST_TASK_DB)) {
                $workerArgs += @('--db', ('"' + $env:HARVEST_TASK_DB + '"'))
            }
            $worker = Start-Process -FilePath $python -ArgumentList $workerArgs `
                -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $runtimeDir 'local-worker.out.log') `
                -RedirectStandardError (Join-Path $runtimeDir 'local-worker.err.log')
        } finally {
            Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
        $worker.Refresh()
        if ($worker.HasExited) {
            throw "后台接单服务启动后立即退出，请查看 .runtime\local-worker.err.log。"
        }
        Write-Host "后台接单服务已启动（PID $($worker.Id)）。"
    }

    if (Test-LocalPort 8000) {
        Write-Host '网页服务已在 http://127.0.0.1:8000 运行，不重复启动。'
    } else {
        Write-Host '正在启动网页服务：http://127.0.0.1:8000'
        & $python -m server.start_server
        if ($LASTEXITCODE -ne 0) { throw "网页服务退出，代码 $LASTEXITCODE" }
    }
} finally {
    Pop-Location
}
