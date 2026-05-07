# ai4qz

本地控制 qz notebook 的最小可用项目。

当前版本已经覆盖两条可用链路：

- 通过 Jupyter `terminals` REST + WebSocket 在 notebook 里执行命令
- 通过 Jupyter `contents` API 上传和下载文件

这版不再依赖 SSH、`wstunnel` 或额外暴露端口，直接走已经验证可用的网页 notebook 接口。

## 安装

```bash
cd /Users/wty25057/workspace/ai4qz
python3 -m pip install -e .
```

不想安装也可以直接运行：

```bash
cd /Users/wty25057/workspace/ai4qz
PYTHONPATH=src python3 -m ai4qz.cli list
```

## 配置

默认配置文件是 [configs/notebooks.yaml](/Users/wty25057/workspace/ai4qz/configs/notebooks.yaml)。

默认约定：

- cookies 放在 [cookies/README.md](/Users/wty25057/workspace/ai4qz/cookies/README.md) 对应目录
- HAR 放在 [har/README.md](/Users/wty25057/workspace/ai4qz/har/README.md) 对应目录
- 这些敏感文件默认不会提交进 git

建议优先给每台 notebook 配好 `base_url`。如果你想尽量少填，项目也支持“最小配置”。

最小配置时，每台 notebook 只需要：

- `name`
- `entry_url`

再在 `defaults` 里提供：

- `cookies_file`
- 可选 `har_file`

自动补全顺序：

1. 从 `entry_url` 提取 `notebook_id`
2. 用 `cookies_file` 里的 path 推断当前 Jupyter `base_url`
3. 如果 cookies 推断失败，再用 `har_file` 搜索 `base_url`
4. 如果 cookies 里存在多个历史 session，优先选择实际探测可用的那个 `base_url`

最小配置示例见 [configs/notebooks.minimal.yaml](/Users/wty25057/workspace/ai4qz/configs/notebooks.minimal.yaml)。

实际经验上，最小配置建议同时给 `defaults.har_file`，因为 cookies 里常常会残留多个旧 session path，HAR 更适合兜底当前有效地址。

## 常用命令

列出配置里的 notebook：

```bash
PYTHONPATH=src python3 -m ai4qz.cli list
```

检查某台 notebook 的 cookies、`_xsrf`、`terminals`、`contents`：

```bash
PYTHONPATH=src python3 -m ai4qz.cli check h200_ncu
```

深度检查，实际执行一条 `pwd`：

```bash
PYTHONPATH=src python3 -m ai4qz.cli check h200_ncu --deep
```

执行单台命令：

```bash
PYTHONPATH=src python3 -m ai4qz.cli run h200_ncu --cmd 'pwd && whoami'
```

按 tag 批量 fan-out：

```bash
PYTHONPATH=src python3 -m ai4qz.cli fanout --tag active --cmd 'hostname'
```

上传文件：

```bash
PYTHONPATH=src python3 -m ai4qz.cli upload h200_ncu ./local.txt remote/dir/local.txt
```

下载文件：

```bash
PYTHONPATH=src python3 -m ai4qz.cli download h200_ncu remote/dir/local.txt ./downloads/local.txt
```

打印解析后的 `base_url`：

```bash
PYTHONPATH=src python3 -m ai4qz.cli discover h200_ncu
```

用最小配置文件运行：

```bash
ai4qz --config ./configs/notebooks.minimal.yaml discover h200_ncu
```

## 长期 Shell

当前版本已经支持持久 terminal session。

可用命令：

```bash
ai4qz --config ./configs/notebooks.minimal.yaml session-open h200_ncu
ai4qz --config ./configs/notebooks.minimal.yaml session-list
ai4qz --config ./configs/notebooks.minimal.yaml session-run <session_id> --cmd 'pwd && whoami'
ai4qz --config ./configs/notebooks.minimal.yaml session-attach <session_id>
ai4qz --config ./configs/notebooks.minimal.yaml session-close <session_id>
```

行为说明：

- `session-open`
  - 创建一个持久 Jupyter terminal
  - 默认优先尝试启用 `tmux`
  - 如果远端没有 `tmux`，自动降级为裸 terminal 持久会话
- `session-run`
  - 在同一个持久 terminal 里反复执行命令
  - 不会删除 terminal，因此可以保留上下文
- `session-attach`
  - 把本地终端直接附着到远端 terminal
  - 脱离快捷键是 `Ctrl-]`
- `session-close`
  - 显式删除远端 terminal，并移除本地 session 记录

本地 session 状态会保存在项目下的 `.ai4qz/sessions.json`。

### 长期 Shell 的边界

- 如果远端 notebook/container 重启，原 terminal 会丢失，session 无法恢复
- 如果远端有 `tmux`，重连体验会更稳；如果没有，也能工作，但上下文恢复能力较弱
- 交互 attach 目前更适合常规 shell 命令；全屏 TUI 程序属于尽力支持，不保证完全稳定
- `session-run` 适合“复用同一个 shell 上下文执行多条命令”
- `upload/download` 仍然是文件传输的主路径，不建议长期 shell 承担大文件同步

## 约束

- `run`/`fanout` 面向非交互式单条命令，不适合长期交互 shell
- 命令输出来自 TTY 流，严格说是“终端输出”，不是分离后的纯 `stdout/stderr`
- 上传/下载默认走 base64，因此文本和二进制都能处理

## 后续扩展建议

- 多账号时按账号拆分 cookies 文件
- 每台 notebook 固定一个 `name` 和若干 `tags`
- notebook 重启后 `base_url` 可能漂移，建议保留 `har_file` 作为兜底
- 如果你后面要让我同时控制多台 notebook，先把需要的信息按 [NOTEBOOK_INFO.md](/Users/wty25057/workspace/ai4qz/NOTEBOOK_INFO.md) 填好
