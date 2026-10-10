<div align="center">

---

> 此 repo 为 Kaggle 2026 竞赛项目程序，遵循 Kaggle Competition Rule 开源协定。*

---

## 快速开始：下载数据集

本仓库不含数据。数据集是 Kaggle 上一个 265 GB 压缩包（解压后 569.76 GB、819,640 个文件），建议用 Kaggle CLI 拉取下载。运行以下命令检查 Kaggle CLI 并下载数据集：

```bash
# Linux / macOS / WSL / Git-Bash
./download.sh --dest <dataset_path>

# Windows
download.bat --dest <dataset_path>
```

- 首次运行会依次：复用或安装依赖（优先 [uv](https://docs.astral.sh/uv/)，否则在仓库内建 `.venv` + pip）→ 把 `--dest` 写入 `.env` 的 `RAW_DIR` → 检查目标磁盘空间（默认要求 600 GB 可用）→ 引导 `kaggle auth login`（浏览器 OAuth；本机没有 `kaggle` CLI 时脚本会临时用 `uvx` 代装，SSH 环境自动改用「打印链接 + 粘贴验证码」流程）→ 开始下载。运行日志写在 `<dest>/download-<UTC>.log`。
- **可断点续传**：重新执行**同一条命令**即可续传。已在磁盘上且大小正确的文件会被跳过；结束时打印 `[verified]`（全部通过）或 `[INCOMPLETE]`（有缺失，重跑修复）。
- 必须先加入比赛并在 Kaggle 上**接受比赛规则**，否则下载会返回 403。
- 拉取 3 个文件做测试：`./download.sh --dest /tmp/rsna-smoke --limit 3`。
- 下载器自身的参数（`--limit`、`--write-workers`、`--verify-only` 等）可直接跟在后面，`./download.sh -- --help` 查看全部。
- 参考速率 10–40 MB/s（取决于磁盘与网络），全量约 4–15 小时。长期跑建议放后台：`nohup ./download.sh --dest /data/rsna >/dev/null 2>&1 &`，进度看日志。
- Windows 注意：路径名存在 260 字符上限，目标目录要短（如 `D:/rsna`）；给 `<dest>` 开启 **Windows Defender 排除项** 能明显减少掉速。

退出码、认证模型、代理/镜像、故障排查等细节见 [docs/background/download.md](docs/background/download.md)。

---
