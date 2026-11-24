# REA bootstrap：下载官方便携版 Python（embeddable）到用户目录，解压即用。
# 输出：可用的 python.exe 绝对路径（供 install-windows.cmd 捕获）；失败时非零退出。
# 下载源依次尝试：python.org 官方 → 华为云镜像 → npmmirror 镜像。
# 断点续传：中断后重跑，从已下载字节继续（Range 请求）。
# 校验：python.org 发布页公布的官方 MD5（3.12.10 embed-amd64）。

$ErrorActionPreference = "Stop"
$version = "3.12.10"
$fname   = "python-$version-embed-amd64.zip"
$md5     = "fe8ef205f2e9c3ba44d0cf9954e1abd3"
$minSize = 10 * 1024 * 1024   # 官方 10.6 MB，低于此视为不完整

$base = Join-Path $env:LOCALAPPDATA ".rea-tools\python-embed"
$dest = Join-Path $base $version
$py   = Join-Path $dest "python.exe"

# 已就绪则直接复用（幂等）
if (Test-Path $py) {
    & $py -c "import argparse, ssl, subprocess, urllib.request, winreg, sys; assert sys.version_info >= (3, 10)" 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Output $py; exit 0 }
}

$urls = @(
    "https://www.python.org/ftp/python/$version/$fname",
    "https://mirrors.huaweicloud.com/python/$version/$fname",
    "https://registry.npmmirror.com/-/binary/python/$version/$fname"
)

New-Item -ItemType Directory -Force -Path $base | Out-Null
$zip = Join-Path $base $fname
$ProgressPreference = "SilentlyContinue"

function Get-FileWithResume([string]$url, [string]$out) {
    # 断点续传：单源最多 5 次，从已下载字节继续；返回是否完整
    for ($i = 1; $i -le 5; $i++) {
        $have = 0
        if (Test-Path $out) { $have = (Get-Item $out).Length }
        try {
            $req = [System.Net.HttpWebRequest]::Create($url)
            $req.UserAgent = "rea-setup/1.0"
            $req.Timeout = 60000
            $req.ReadWriteTimeout = 120000
            if ($have -gt 0) {
                $req.AddRange($have)
                Write-Host "  resume from $([math]::Round($have/1MB,1)) MB (attempt $i)"
            } else {
                Write-Host "  download (attempt $i)"
            }
            $resp = $req.GetResponse()
            $remoteSize = $resp.ContentLength + $have
            $stream = $resp.GetResponseStream()
            $mode = "Create"
            if ($have -gt 0 -and $resp.StatusCode -eq [System.Net.HttpStatusCode]::PartialContent) { $mode = "Append" }
            if ($mode -eq "Append") {
                $fs = [System.IO.File]::Open($out, [System.IO.FileMode]::Append)
            } else {
                $fs = [System.IO.File]::Open($out, [System.IO.FileMode]::Create)
            }
            $buf = New-Object byte[] 1048576
            while (($n = $stream.Read($buf, 0, $buf.Length)) -gt 0) {
                $fs.Write($buf, 0, $n)
                $have += $n
                if ($remoteSize -gt 0) {
                    Write-Progress -Activity "python embeddable" -Status ("{0:P0}" -f ($have / $remoteSize)) -PercentComplete ($have * 100 / $remoteSize)
                }
            }
            $fs.Close(); $stream.Close(); $resp.Close()
            Write-Progress -Activity "python embeddable" -Completed
            if ($have -ge $minSize) { return $true }
            Write-Host "  incomplete ($([math]::Round($have/1MB,1)) MB), retrying"
        } catch {
            Write-Host "  interrupted: $($_.Exception.Message)"
        }
    }
    return $false
}

$downloaded = $false
foreach ($u in $urls) {
    Write-Host "  source: $u"
    if (Get-FileWithResume $u $zip) { $downloaded = $true; break }
    # 换源前清掉不完整残留，避免下一源从错误偏移续传
    if (Test-Path $zip) { Remove-Item $zip -Force }
}
if (-not $downloaded) {
    Write-Host "BOOTSTRAP_FAILED: all sources exhausted (partial file kept at $zip)"
    exit 1
}

# 官方 MD5 校验
$md5svc = [System.Security.Cryptography.MD5]::Create()
try {
    $fs = [System.IO.File]::OpenRead($zip)
    $hash = [BitConverter]::ToString($md5svc.ComputeHash($fs)).Replace("-", "").ToLower()
    $fs.Close()
} finally { $md5svc.Dispose() }
if ($hash -ne $md5) {
    Write-Host "BOOTSTRAP_FAILED: MD5 mismatch ($hash) - file corrupted, delete $zip and rerun"
    exit 1
}
Write-Host "  MD5 verified"

if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath $dest -Force
Remove-Item $zip -Force

# 功能自检：安装器需要的模块全部可用
& $py -c "import argparse, ssl, subprocess, urllib.request, winreg, sys; assert sys.version_info >= (3, 10)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "BOOTSTRAP_FAILED: portable python self-check failed"
    exit 1
}
Write-Output $py
exit 0
