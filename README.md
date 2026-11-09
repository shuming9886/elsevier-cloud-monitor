# Elsevier Cloud Monitor

## 把 Track 链接交给 Agent，稿件变化自动发邮件

监控一篇 Elsevier 投稿，只需要它的 AuthorHub Track 链接。把链接交给 Codex、Claude Code 或 Cursor，Agent 会自动将监控部署到你的 Linux 云服务器；审稿状态发生变化时，系统会发送邮件提醒。

不用反复刷新页面，不用自己写脚本，也不需要让大模型持续运行。

## 安装 Skill

将下面这句话发送给你的 Agent：

```text
帮我安装这个 Skill：
https://github.com/shuming9886/elsevier-cloud-monitor
```

安装完成后，重新开始一个会话或重启 Agent，使 Skill 生效。

支持的工具包括：

- Codex
- Claude Code
- Cursor
- 其他兼容 Agent Skills 标准的编程 Agent

## 使用方法

安装后，将类似下面的请求发送给 Agent：

```text
请使用 elsevier-cloud-monitor，把我的 Elsevier 投稿状态监控部署到云服务器。
```

接下来按照 Agent 的提示提供：

- Elsevier AuthorHub 追踪链接
- 云服务器连接信息
- 接收提醒的邮箱
- 邮箱 SMTP 授权信息

Agent 会负责检查服务器、安装依赖、配置监控、发送测试邮件，并验证服务是否正常运行。用户不需要手动编写配置文件或执行部署命令。

## 服务器要求

当前支持：

- Debian 12
- Ubuntu 22.04 / 24.04
- x86_64 / amd64
- systemd
- SSH 密钥登录
- root 或具备免密 `sudo` 权限的用户

暂不支持 ARM、CentOS/RHEL、Windows Server 和 Docker 部署。

## 工作方式

部署完成后，服务器会定期访问 Elsevier AuthorHub 页面并保存上一次有效状态。当状态发生变化时，系统会向指定邮箱发送通知。

首次运行只建立状态基线，不会发送误报。页面加载失败、稿件不存在或页面内容不完整时，不会覆盖上一次有效状态。

## 隐私与安全

Elsevier AuthorHub 追踪链接可能包含论文题目、作者、期刊、稿件编号和审稿进度，应作为敏感信息处理。

- 不要把真实追踪链接发布到公开 Issue 或社交平台
- 不要把 SMTP 授权码提交到 GitHub
- 建议使用邮箱授权码或 App Password，而不是邮箱登录密码
- Skill 不会把真实链接、邮箱凭据或稿件状态写入仓库
- 监控服务运行在用户自己的云服务器上

如果 Agent 所在平台没有安全的凭据输入能力，应避免直接在公开或会被共享的对话中发送 SMTP 授权码。

## 设计原则

- Agent 只负责部署和验证
- Python 与 systemd 负责长期监控
- 不使用大模型判断状态变化
- 不在无效页面上更新状态
- 以独立的非特权 Linux 用户运行
- 默认每 30 分钟检查一次

## 开发与测试

项目包含自动化测试，用于检查页面解析、状态保存、环境配置、服务权限和部署安全。当前版本通过 25 项单元测试。

```bash
python3 -m unittest discover -s tests -v
```

## 免责声明

本项目不是 Elsevier 官方产品，与 Elsevier 无隶属、合作或背书关系。页面结构发生变化时，监控程序可能需要更新。

## License

MIT
