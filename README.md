<div align="center">

<h1>RSNA Knee Abnormality Detection</h1>

![authors](https://img.shields.io/badge/authors-Aloudname-162b3e?style=flat-square)
![status](https://img.shields.io/badge/status-Developing-ffdd00?style=flat-square)
![license](https://img.shields.io/badge/Kaggle-Rules-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-3.11+-blue?logo=python&logoColor=white)
<!-- ![PyTorch](https://img.shields.io/badge/pytorch-2.0+-ee4c2c?logo=pytorch&logoColor=white) -->

</div>

---

> 此 repo 为 Kaggle 2026 竞赛项目程序，遵循 Kaggle Competition Rule 开源协定。*

---

## 快速开始：下载数据集

刚 clone 时仓库里没有任何数据。数据集是 Kaggle 上的一个 265 GB 压缩包（解压后 569.76 GB、819,640 个文件），下面一条命令完成「准备环境 → 引导认证 → 预检 → 边下边解 → 校验」：

```bash
# Linux / macOS / WSL / Git-Bash
./download.sh --dest /data/rsna

# Windows（cmd、PowerShell、双击本文件均可）
download.bat --dest D:/rsna
```

- 首次运行会依次：复用或安装依赖（优先 [uv](https://docs.astral.sh/uv/)，否则在仓库内建 `.venv` + pip）→ 把 `--dest` 写入 `.env` 的 `RAW_DIR` → 检查目标磁盘空间（默认要求 600 GiB 可用）→ 引导 `kaggle auth login`（浏览器 OAuth；本机没有 `kaggle` CLI 时脚本会临时用 `uvx` 代装，SSH 环境自动改用「打印链接 + 粘贴验证码」流程）→ 开始下载。运行日志写在 `<dest>/download-<UTC>.log`。
- **可断点续传**：任何时候中断（Ctrl+C、断网、重启），重新执行**同一条命令**即可续传。已在磁盘上且大小正确的文件会被跳过；结束时打印 `[verified]`（全部通过）或 `[INCOMPLETE]`（有缺失，重跑修复）。
- 必须先加入比赛并在 Kaggle 上**接受比赛规则**，否则下载会返回 403。
- 只拉 300 个文件做冒烟测试：`./download.sh --dest /tmp/rsna-smoke --limit 300`。
- 下载器自身的参数（`--limit`、`--write-workers`、`--verify-only` 等）可直接跟在后面，`./download.sh -- --help` 查看全部。
- 参考速率 10–40 MB/s（取决于磁盘与网络），全量约 4–15 小时。长期跑建议放后台：`nohup ./download.sh --dest /data/rsna >/dev/null 2>&1 &`，进度看日志。
- Windows 注意：目标目录要短（如 `D:/rsna`），路径过深会撞上 260 字符上限；给 `<dest>` 加一个 Windows Defender 排除项能明显减少掉速。

退出码、认证模型、代理/镜像、故障排查等细节见 [docs/background/download.md](docs/background/download.md)。

---
