# ai4qz

本地控制 qz notebook 的命令行工具，通过 Jupyter REST + WebSocket API 实现，无需 SSH 或端口转发。

## 安装

```bash
pip install -e .
```

依赖：`PyYAML`、`requests`、`websocket-client`、`pyte`、`urwid`。

## 配置

默认配置文件：`configs/notebooks.yaml`。

敏感文件约定：

- cookies 放在 `cookies/` 目录
- HAR 放在 `har/` 目录
- 这些文件不会提交进 git

### 获取 Cookies 文件

1. 在浏览器中登录 `https://qz.sii.edu.cn`，进入目标 notebook 的 Jupyter 页面
2. 打开浏览器开发者工具（F12）→ Application → Cookies，确认 `qz.sii.edu.cn` 和实际 notebook 域名下都有 cookie
3. 安装浏览器扩展导出 Netscape 格式 cookies：
   - Chrome: [Get cookies.txt LOCALLY](https://chromecast.com/get-cookies-txt-locally)
   - Firefox: [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)
4. 在 `qz.sii.edu.cn` 页面上点击导出，保存到 `cookies/` 目录（如 `cookies/qz_cookies.txt`）

> 注意：需要同时覆盖门户域名和 notebook 运行域名，否则终端和文件接口可能缺少 `_xsrf`。多账号不要混用同一份 cookies。

### 获取 HAR 文件

1. 在浏览器中打开 `https://qz.sii.edu.cn`，进入目标 notebook 的 Jupyter 页面
2. 打开开发者工具（F12）→ Network 标签页
3. 勾选 "Preserve log"
4. 在 notebook 中执行一些操作（打开终端、浏览文件等），确保 Network 中捕获到 `/api/terminals`、`/api/contents` 等请求
5. 右键 Network 列表 → "Save all as HAR with content"，保存到 `har/` 目录（如 `har/qz.sii.edu.cn.har`）

> HAR 文件用于 cookies 推断 `base_url` 失败时的兜底，也能应对 notebook 重启后 `base_url` 漂移的情况。

### 最小配置

每台 notebook 只需提供 `name` + `entry_url`，再在 `defaults` 里提供 `cookies_file` 和可选的 `har_file`。

自动发现顺序：

1. 从 `entry_url` 提取 `notebook_id`
2. 用 `cookies_file` 里的 path 推断 `base_url`
3. cookies 推断失败时，用 `har_file` 搜索 `base_url`
4. 多个历史 session 时，优先选择探测可用的 `base_url`

示例见 `configs/notebooks.minimal.yaml`。

## 命令总览

```
ai4qz [--config PATH] [--json] [--timeout SEC] <subcommand> ...
```

| 全局参数 | 说明 |
|---------|------|
| `--config` | 配置文件路径（默认 `configs/notebooks.yaml`） |
| `--json` | 输出 JSON 格式 |
| `--timeout` | 命令超时秒数，覆盖配置文件中的 `command_timeout_sec` |

## 命令详情

### 发现与检查

```bash
# 列出配置中的 notebook
ai4qz list

# 解析并打印 base_url
ai4qz discover h200_ncu

# 检查 cookies、xsrf、terminals、contents API
ai4qz check h200_ncu

# 深度检查（实际执行 pwd 探测）
ai4qz check h200_ncu --deep
```

### 执行命令

```bash
# 单台执行
ai4qz run h200_ncu --cmd 'nvidia-smi'

# 自定义超时（覆盖配置文件的 60s 默认值）
ai4qz --timeout 300 run h200_ncu --cmd 'python3 train.py'

# 按 tag 批量执行
ai4qz fanout --tag active --cmd 'hostname'

# 指定多台 + 自定义并发数
ai4qz fanout --targets h200_ncu,qz_dev --concurrency 2 --cmd 'uptime'
```

### 文件传输

```bash
# 上传
ai4qz upload h200_ncu ./local.txt /remote/dir/local.txt

# 下载到指定路径
ai4qz download h200_ncu /remote/dir/file.txt ./downloads/file.txt

# 下载到当前目录（省略 local_path，自动使用远端文件名）
ai4qz download h200_ncu /remote/dir/file.txt

# contents API 不可用时，自动回退到终端 base64 传输
# 也可手动指定：
ai4qz download h200_ncu /remote/dir/file.txt --via-terminal
```

> 下载时如果文件不在 Jupyter 内容根目录下（也就是你登陆时候显示的那个目录）（如 `/inspire/hdd/...`），contents API 会返回 404，此时自动回退到终端 base64 传输方式。

### 持久会话

```bash
# 创建会话（默认不启用 tmux）
ai4qz session-open h200_ncu

# 创建会话并启用 tmux
ai4qz session-open h200_ncu --tmux

# 指定工作目录和 tmux 会话名
ai4qz session-open h200_ncu --cwd /data --tmux --tmux-name mywork

# 创建会话并直接进入交互式 TUI
ai4qz session-open h200_ncu --tui

# 列出所有会话
ai4qz session-list

# 在已有会话中执行命令
ai4qz session-run <session_id> --cmd 'ls -la'

# 附着到会话（原始终端模式，Ctrl-] 脱离）
ai4qz session-attach <session_id>

# 关闭会话
ai4qz session-close <session_id>
```

本地会话状态保存在 `.ai4qz/sessions.json`。

### 交互式 TUI 模式

`session-open --tui` 启动基于 urwid + pyte 的全功能交互终端：

```bash
ai4qz session-open h200_ncu --tui
```

TUI 功能：

- 完整 VT220 终端仿真，支持颜色、粗体、下划线、反色等样式
- 支持所有按键：Ctrl+C、Ctrl+Z、Tab 补全、方向键、F1-F12 等
- 10000 行滚动回溯（Page Up / Page Down）
- 自动跟随终端大小调整（SIGWINCH）
- 状态栏显示：目标名称、会话 ID、终端尺寸、滚动位置
- Ctrl+Q 脱离（会话保留，可重新附着）

## 注意事项

- `run`/`fanout` 面向非交互式单条命令，不适合长期交互
- `--json` 必须放在子命令之前：`ai4qz --json check h200_ncu`
- `--timeout` 必须放在子命令之前：`ai4qz --timeout 300 run ...`
- 命令输出来自 TTY 流，不是分离后的纯 stdout/stderr
- 上传/下载默认走 base64，文本和二进制均可处理
- notebook 重启后 terminal 会丢失，会话无法恢复
- `session-attach`（原始模式）脱离键为 Ctrl-]；`--tui` 模式脱离键为 Ctrl+Q
