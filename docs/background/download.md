# 数据集下载入口

新 clone 的机器上，用 `./download.sh`（Linux / macOS / WSL / Git-Bash）或 `download.bat`（Windows）跑一条命令，脚本依次完成：准备环境 → 引导认证 → 预检 → 边下边解 → 校验。为什么不能直接用 `kaggle competitions download`，见 [kaggle_cli.md](kaggle_cli.md) 与 [../dataset.md](../dataset.md)。

```bash
./download.sh --dest /data/rsna                    # Linux / macOS / WSL / Git-Bash
download.bat --dest D:/rsna                        # Windows（cmd / PowerShell / 双击）
./download.sh --dest /tmp/rsna-smoke --limit 300   # 冒烟测试
```

## 文件结构

| 文件                                                  | 职责                                                                                                                                                                                                                                                          |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`download.sh`](../../download.sh)                   | POSIX sh，只负责找解释器。<br />（1）uv：`uv run --project <repo> --frozen python`；<br />（2）无 uv：选 PATH 上**版本最高**的 Python（≥3.8）；<br />（3）皆无：打印各平台的 uv / Python 安装指引，退出 1。<br />`exec` 交接，Ctrl+C 直达子进程。 |
| [`download.bat`](../../download.bat)                 | （1）uv：`where uv` 命中则 `uv run ...`；<br />（2）否则 `py -3`、再退到  `python`（都校验 ≥3.8）；<br />（3）失败时 `pause`。                                                                                                                  |
| [`scripts/bootstrap.py`](../../scripts/bootstrap.py) | 仅用 Python ≥3.8 标准库的后端实现。                                                                                                                                                                                                                         |

**参数透传**：`bootstrap.py` 只解析自己的参数，其余原样按序转发给 `scripts/download_dataset.py`。

```bash
./download.sh --dest /data/rsna --limit 300 --write-workers 32
./download.sh -- --help          # 下载器自己的参数说明
```

| bootstrap 参数                | 说明                                                                                                 |
| ----------------------------- | ---------------------------------------------------------------------------------------------------- |
| `--dest PATH`               | 目标目录，同时写回`.env` 的 `RAW_DIR`                                                           |
| `--yes` / `-y`            | 一路 yyy（脚本 / CI 用）                                                                             |
| `--force`                   | 预检不通过也继续；也用来抢占残留的运行锁                                                             |
| `--need-gb N`               | 需要的可用空间（GB），默认 600；透传参数里有`--limit`/`--verify-only`/`--dry-run` 时自动降到 5 |
| `--log PATH` / `--no-log` | 日志位置（默认`<dest>/download-<UTC>.log`）/ 不写日志                                              |
| `--skip-auth-check`         | 跳过 Auth 检查                                                                                       |
| `--relogin`                 | 忽略已有凭据，强制`kaggle auth login`                                                              |
| `--check-longpaths`         | 测试用，非 Windows 模拟长路径                                                                        |

## 退出码

| 码  | 含义                                                                                           | 怎么办                                                      |
| --- | ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| 0   | 全部条目落盘并通过校验（日志末尾`[verified]`）                                               | 无需操作                                                    |
| 1   | 下载失败，或校验发现缺失、损坏（`[INCOMPLETE]`）；**bootstrap 没能备好环境也报这个码** | 重跑同一命令。完成的文件跳过，结束时再校验一遍              |
| 2   | 预检拒绝（磁盘不足 / Windows 路径过深 / 目标在仓库内 / 未指定目标），或同目录已有任务在跑      | 按提示调整`--dest`、`--force`、`--need-gb`            |
| 3   | 缺少凭据，且当前环境不允许交互登录                                                             | 在终端里跑提示给的`kaggle auth login`，或加 `--relogin` |
| 130 | 被中断                                                                                         | 重跑同一命令                                                |

## 认证模型

- 只支持 **OAuth**：`kaggle auth login` 把凭据缓存到 `~/.kaggle/credentials.json`（含 `refresh_token`，权限 600），下载器用 `kagglesdk` 读取并自动续期。**不使用 `kaggle.json` / API key**。
- bootstrap 只从凭据里读 `username` 用于打印，不会打印 token；登录子进程直接继承终端，授权码不进日志。
- 本机没有 `kaggle` CLI 时：有 `uv` 就 `uv tool run kaggle auth login`（临时环境，不碰 `pyproject.toml` / `uv.lock` / `.venv`）；两者都没有才在 `.venv` 里装 CLI。
- 无 `DISPLAY` / `WAYLAND_DISPLAY` 时自动加 `--no-launch-browser`：打印链接，浏览器里授权后把验证码粘回终端，SSH 可用。
- `--yes` 或非交互环境不会自动进入登录流程，只打印命令并以 3 退出。
- 凭据失效时用 `./download.sh --relogin` 重登（默认开启的深检会跑 `kaggle auth print-access-token`，**stdout 全丢弃**）。不要删除 `~/.kaggle/credentials.json`：重新登录需要交互式浏览器或验证码。

## 续传与校验语义

- 跳过判定**只比大小**：文件存在且 `st_size` 等于压缩包里的原始大小就算完成。
- 于是：中断留下的**撕裂文件**（大小不符）下次重下；大小**恰好相同**但内容损坏的文件不会被发现，由下载结束时的 `[verified]` / `[INCOMPLETE]` 兜底。
- `--verify-only` 只做最后校验，用于事后确认或定位缺失。默认核对**整个**压缩包（819,640 条目），所以只跑过冒烟测试（如 `--limit 30`）的树会报大批 missing、退出 1，**这是预期的**；想只核对前 N 条，带上同样的 `--limit 300`。
- 运行锁 `<dest>/.download.lock` 防止两个任务写同一棵树（`--verify-only` / `--dry-run` 不加锁）；进程消失后残留的锁会被接管。

## 依赖：uv 与 venv+pip 两条路径

1. 当前解释器能 `import requests, dotenv, kagglesdk` 就直接用（走 `download.sh` 时即 `uv run` 的 `.venv`）。
2. 有 `uv`，就 `uv sync --frozen`（cwd=仓库根），用 `.venv` 里的解释器。`--frozen` 只按 `uv.lock` 安装，不重新解析、不改锁文件。
3. 没有 `uv`、Python ≥3.9 ，就 `python -m venv .venv` + `pip install`（依赖从 `pyproject.toml` 的 `dependencies = [...]` 解析）。Debian/Ubuntu 缺 `venv` 模块时提示 `sudo apt install python3-venv python3-pip`。
4. 都没有，就打印安装指引，退出 1。

镜像与代理 **原样继承** 环境变量，不做硬编码；慢网 / 国内环境可以：

```bash
export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
export PIP_INDEX_URL=$UV_INDEX_URL
export UV_PYTHON_INSTALL_MIRROR=https://ghproxy.com/https://github.com/astral-sh/python-build-standalone/releases/download
export HTTPS_PROXY=http://127.0.0.1:7890
./download.sh --dest /data/rsna
```

## 日志

- 默认写在 `<dest>/download-<UTC>.log`。
- 终端输出与日志一致（tee）。GCS 签名 URL 由下载器脱敏。

## 故障排查

| 现象                                                              | 原因与处理                                                                                                                                                                                                                                    |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 立刻 403 /`You must accept the competition rules`               | 还没加入比赛或没在网页上接受规则。去比赛页面点「Join / I Understand and Accept」后重跑。                                                                                                                                                      |
| `[auth] the stored credentials did not produce an access token` | 凭据过期或被吊销：`./download.sh --relogin`。                                                                                                                                                                                               |
| HTTP 429                                                          | Kaggle 的动态限流。等几分钟再重跑；下载器对 5xx 和超时有重试。                                                                                                                                                                                |
| `OSError [WinError 206]` / 文件名过长（Windows）                | 目标目录太深：换短根目录（`--dest C:/rsna`），或开启长路径支持后重启：`reg add HKLM\SYSTEM\CurrentControlSet\Control\FileSystem /v LongPathsEnabled /t REG_DWORD /d 1 /f`。bootstrap 开始前预警。                                         |
| Windows 上速度忽高忽低                                            | 实时扫描（Defender）在逐个检查上百万个小文件。给`<dest>` 加排除项。                                                                                                                                                                         |
| Ctrl+C 后终端像卡住几秒                                           | 第一次 Ctrl+C 是干净停止：下载器不再发起新请求、排空已入队的写入，打印`[interrupted] ... re-run the same command to resume`，退出码 130。bootstrap 最多等 20 s；超时，或隔 2 s 后再按一次，就直接强杀。写好的文件下次跳过，撕裂的文件重下。 |
| 速度只有几 MB/s                                                   | 看日志里的`net` / `disk`：`net` 高而 `disk` 低，瓶颈在磁盘（机械盘、被别的任务占用）；`net` 低才是网络问题。可调 `--write-workers`。                                                                                              |
| 想确认数据是否完整                                                | `./download.sh --dest /data/rsna --verify-only`，看 `[verified]` / `[INCOMPLETE]`。                                                                                                                                                     |
