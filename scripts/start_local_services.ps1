param(
    # Stop this project's worker and Web processes first, then start fresh
    # (needed after code updates; used by 重启本地服务.bat).
    [switch]$Restart
)

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

function Stop-ProjectServices {
    $targets = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'python.exe' -and
        $_.CommandLine -like "*$python*" -and
        ($_.CommandLine -like '*-m server.agent_worker*' -or $_.CommandLine -like '*-m server.start_server*')
    })
    if ($targets.Count -eq 0) {
        Write-Host '没有找到正在运行的本项目接单进程或网页进程。'
    }
    foreach ($process in $targets) {
        Write-Host "停止旧进程 PID $($process.ProcessId)：$($process.CommandLine)"
        # /T also ends the interpreter child that a venv python.exe launcher starts.
        & taskkill.exe /PID $process.ProcessId /T /F | Out-Null
    }
    for ($attempt = 0; $attempt -lt 20 -and (Test-LocalPort $webPort); $attempt++) {
        Start-Sleep -Milliseconds 500
    }
    if (Test-LocalPort $webPort) {
        throw "$webPort 端口仍被占用，可能是其他程序或旧窗口启动的网页服务。请关闭它后再双击重启。"
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
$transcript = Join-Path $runtimeDir 'launcher.log'
try { Start-Transcript -LiteralPath $transcript -Force | Out-Null } catch { $transcript = $null }
try {
    $configuredPort = & $python -c "from server.local_service_config import load_web_config; print(load_web_config()['port'])"
    if ($LASTEXITCODE -ne 0) { throw '无法读取网页端口配置；服务未启动。' }
    $webPort = [int]$configuredPort
    $webAddress = "http://127.0.0.1:$webPort"
    if ($Restart) { Stop-ProjectServices }
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
            $workerArgs = @('-m', 'server.agent_worker', '--base-url', $webAddress)
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
        # The worker runs a dependency self-check before claiming any ticket and
        # prints an ASCII marker once it passes; a failed check exits the process.
        $workerLog = Join-Path $runtimeDir 'local-worker.out.log'
        $selfCheckPassed = $false
        for ($attempt = 0; $attempt -lt 120; $attempt++) {
            Start-Sleep -Milliseconds 500
            $worker.Refresh()
            if ($worker.HasExited) { break }
            if ((Test-Path -LiteralPath $workerLog) -and
                (Select-String -LiteralPath $workerLog -SimpleMatch 'self-check passed' -Quiet)) {
                $selfCheckPassed = $true
                break
            }
        }
        if ($worker.HasExited) {
            throw "后台接单服务启动后退出（多为启动自检未通过，例如缺少依赖或浏览器），请查看 .runtime\local-worker.err.log。"
        }
        if ($selfCheckPassed) {
            Write-Host "后台接单服务已通过启动自检并启动（PID $($worker.Id)）。"
        } else {
            Write-Warning "后台接单服务仍在启动自检（PID $($worker.Id)），请稍后在网页顶部确认是否在线。"
        }
    }

    if (Test-LocalPort $webPort) {
        Write-Host "网页服务已在 $webAddress 运行，不重复启动；已为你打开网页。"
        Write-Host '更新代码后需要重启才会生效：请双击「重启本地服务.bat」。'
        Start-Process $webAddress
        Read-Host '按回车键关闭此窗口' | Out-Null
    } else {
        Write-Host "正在启动网页服务：$webAddress"
        & $python -m server.start_server
        if ($LASTEXITCODE -ne 0) { throw "网页服务退出，代码 $LASTEXITCODE" }
    }
} finally {
    Pop-Location
    if ($transcript) { try { Stop-Transcript | Out-Null } catch { } }
}
