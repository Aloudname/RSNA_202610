# 数据集下载入口

`./download.sh`（Linux / macOS / WSL / Git-Bash）与 `download.bat`（Windows）是给新 clone 用户的**唯一入口**，一条命令完成：准备环境 → 引导认证 → 预检 → 边下边解 → 校验。背景（为什么不能直接 `kaggle competitions download`）见 [kaggle_cli.md](kaggle_cli.md) 与 [../dataset.md](../dataset.md)。

```bash
./download.sh --dest /data/rsna                 # 三平台里除 Windows 之外
download.bat --dest D:/rsna                     # Windows（cmd / PowerShell / 双击）
./download.sh --dest /tmp/rsna-smoke --limit 300   # 冒烟测试
```

## 三个文件的分工

| 文件 | 职责 |
| --- | --- |
| [`download.sh`](../../download.sh) | 约 60 行 POSIX sh。只会找解释器：有 `uv` 就用 `uv run --project <repo> --frozen python`，否则在 PATH 上挑**版本最高**的 Python（≥3.8），都没有就打印各平台的 uv / Python 安装指引并退出 1。用 `exec` 交接，Ctrl+C 直达子进程。 |
| [`download.bat`](../../download.bat) | 同样只找解释器：`where uv` 命中 → `uv run ...`，否则 `py -3` → `python`（都校验 ≥3.8）。失败时 `pause`，双击窗口不会一闪而过。 |
| [`scripts/bootstrap.py`](../../scripts/bootstrap.py) | 唯一实现，只用标准库、Python ≥3.8 可跑。不依赖 CWD（仓库根由 `__file__` 推导），不被下载器 import。 |

**参数透传**：`bootstrap.py` 只认自己的一小撮参数，其余按原顺序原样转发给 `scripts/download_dataset.py`，无需 `--`：

```bash
./download.sh --dest /data/rsna --limit 300 --write-workers 32
./download.sh -- --help          # 下载器自己的参数说明
```

| bootstrap 参数 | 说明 |
| --- | --- |
| `--dest PATH` | 目标目录，同时写回 `.env` 的 `RAW_DIR` |
| `--yes` / `-y` | 绝不提问；缺东西时直接失败（适合脚本/CI） |
| `--force` | 预检不通过也继续；也用于抢占残留的运行锁 |
| `--need-gb N` | 需要的可用空间（GiB），默认 600；带 `--limit`/`--verify-only`/`--dry-run` 时自动降到 5 |
| `--log PATH` / `--no-log` | 日志位置（默认 `<dest>/download-<UTC>.log`）/ 不写日志 |
| `--skip-auth-check` | 跳过「凭据还能不能换到 token」的额外检查 |
| `--relogin` | 忽略已有凭据，强制重新 `kaggle auth login` |
| `--check-longpaths` | 在非 Windows 上模拟执行长路径预检（测试用） |

## 退出码

| 码 | 含义 | 怎么办 |
| --- | --- | --- |
| 0 | 全部条目落盘并通过校验（日志末尾 `[verified]`） | 完事 |
| 1 | 下载失败，或校验发现缺失/损坏（`[INCOMPLETE]`）；也可能是 bootstrap 没能准备好环境 | 重跑同一命令：已完成文件会跳过，末尾会再校验一遍 |
| 2 | 预检拒绝（磁盘不足 / Windows 路径过深 / 目标在仓库内 / 未指定目标）或已有同目录任务在跑 | 按提示调整 `--dest`、`--force`、`--need-gb` |
| 3 | 缺少凭据且当前环境不允许交互登录 | 在终端里手动跑提示中的 `kaggle auth login`，或加 `--relogin` |
| 130 | 被 Ctrl+C 打断 | 重跑同一命令续传 |

## 认证模型

- 只支持 **OAuth**：`kaggle auth login` 会把凭据缓存到 `~/.kaggle/credentials.json`（含 `refresh_token`，权限 600），下载器用 `kagglesdk` 读取并自动续期。**不使用 `kaggle.json` / API key**。
- bootstrap 只读取其中的 `username` 用于打印，**从不打印 token**；登录子进程直接继承终端，交互内容（授权码）不进日志。
- 本机没有 `kaggle` CLI 时：有 `uv` 就 `uv tool run kaggle auth login`（临时环境，不碰 `pyproject.toml` / `uv.lock` / `.venv`）；两者都没有才在 `.venv` 里装 CLI。
- 无 `DISPLAY` / `WAYLAND_DISPLAY` 时自动加 `--no-launch-browser`：打印链接，浏览器里授权后把验证码粘回终端，SSH 可用。
- `--yes` 或非交互环境**绝不自动进入登录流程**，只打印命令并以 3 退出。
- 凭据失效时（默认开启的深检会跑 `kaggle auth print-access-token`，**stdout 全丢弃**）用 `./download.sh --relogin` 重登。不要删除 `~/.kaggle/credentials.json`：重新登录需要交互式浏览器/验证码。

## 续传与校验语义

- 跳过判定是**仅比大小**：文件存在且 `st_size` 等于压缩包里的原始大小即视为完成。不做内容级校验（否则每次重跑都要读 570 GB）。
- 因此：中断产生的**撕裂文件**（大小不符）下次会被重下；大小**恰好相同**但内容损坏的文件不会被发现——最终一致性由下载结束时的校验输出 `[verified]` / `[INCOMPLETE]` 给出。
- `--verify-only` 只做最后那遍校验，用于事后确认或定位缺失。它默认核对**整个**压缩包（819,640 条目），所以只跑过冒烟测试（如 `--limit 300`）的树会报大批 missing 并退出 1，这是预期的；想只核对前 N 条就带上同样的 `--limit 300`。
- 运行锁 `<dest>/.download.lock` 防止两个任务写同一棵树（`--verify-only` / `--dry-run` 不加锁）；进程消失后残留的锁会自动接管。

## 依赖：uv 与 venv+pip 两条路径

1. 当前解释器已能 `import requests, dotenv, kagglesdk` → 直接用（走 `download.sh` 时即 `uv run` 的 `.venv`）。
2. 有 `uv` → `uv sync --frozen`（cwd=仓库根），用 `.venv` 里的解释器。`--frozen` 表示只按 `uv.lock` 安装，不重新解析、不改锁文件。
3. 没有 `uv`、Python ≥3.9 → `python -m venv .venv` + `pip install`（依赖从 `pyproject.toml` 的 `dependencies = [...]` 解析）。Debian/Ubuntu 缺 `venv` 模块时会提示 `sudo apt install python3-venv python3-pip`。
4. 都没有 → 打印安装指引退出 1。

镜像与代理**原样继承**环境变量，不做硬编码；慢网/国内环境可以：

```bash
export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
export PIP_INDEX_URL=$UV_INDEX_URL
export UV_PYTHON_INSTALL_MIRROR=https://ghproxy.com/https://github.com/astral-sh/python-build-standalone/releases/download
export HTTPS_PROXY=http://127.0.0.1:7890
./download.sh --dest /data/rsna
```

## 日志

- 默认写在 `<dest>/download-<UTC>.log`（如 `download-20261010T024500Z.log`），**每次运行新建**：既有日志绝不会被截断或改名，bootstrap 自身的输出与下载器的输出都进去，行序确定。
- 终端输出与日志内容一致（tee）。日志里不会出现 token；GCS 签名 URL 也会被下载器脱敏成 `https://storage.googleapis.com/...?<redacted>`，方便整段贴给别人看。

## 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 立刻 403 / `You must accept the competition rules` | 还没加入比赛或在网页上接受规则。去比赛页面点「Join / I Understand and Accept」后重跑。 |
| `[auth] the stored credentials did not produce an access token` | 凭据过期或被吊销：`./download.sh --relogin`。 |
| HTTP 429 | Kaggle 的动态限流。等几分钟再重跑；下载器本身对 5xx/超时有重试。 |
| `OSError [WinError 206]` / 文件名过长（Windows） | 目标目录太深：换短根目录（`--dest C:/rsna`），或开启长路径支持后重启：`reg add HKLM\SYSTEM\CurrentControlSet\Control\FileSystem /v LongPathsEnabled /t REG_DWORD /d 1 /f`。bootstrap 会在开始前预警。 |
| Windows 上速度忽高忽低 | 实时扫描（Defender）在逐个检查上百万个小文件。给 `<dest>` 加排除项。 |
| Ctrl+C 后终端像卡住几秒 | 第一次 Ctrl+C 是干净停止：下载器不再发起新请求、排空已入队的写入，并打印 `[interrupted] ... re-run the same command to resume`，退出码 130。bootstrap 最多等 20 s，超时或隔 2 s 后再按一次 Ctrl+C 就强杀。已写好的文件下次会被跳过，撕裂的文件会被重下。 |
| 速度只有几 MB/s | 先看日志里的 `net` / `disk` 两个数字：`net` 高而 `disk` 低说明瓶颈在磁盘（机械盘、被别的任务占用），`net` 低才是网络问题。可调 `--write-workers`。 |
| 想确认数据是否完整 | `./download.sh --dest /data/rsna --verify-only`，看 `[verified]` / `[INCOMPLETE]`。 |

## Windows 手测清单

`download.bat` 只做「找解释器 + 转发 + 失败时 pause」，逻辑都在本机测过的 `bootstrap.py` 里；但它**没有在真实 Windows 机器上跑过**。拿到 Windows 机器时请按顺序确认：

1. 双击 `download.bat`：能起来、报错时窗口不闪退（`pause` 生效）。
2. 在 cmd 里：`download.bat --dest D:/rsna-测试 --limit 50`（含空格与中文的路径）。
3. 在 PowerShell 里执行同样的命令（不需要改 `ExecutionPolicy`）。
4. 在 Git-Bash 里 `./download.bat --limit 50` 与 `./download.sh --limit 50` 都能跑。
5. `download.bat -- --help` 能把 `--help` 转给下载器，而不是被 bootstrap 自己吃掉。
6. Ctrl+C 之后：进程确实退出、`<dest>/download-<UTC>.log` 末尾有 `[interrupted]`、重跑能续传。
