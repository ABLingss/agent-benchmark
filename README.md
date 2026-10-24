# Agent Benchmark

使用自己的 Agent 完成题目，每题20分钟。

1. [推理网关：缓存亲和路由](gateway/TASK.md)
2. [前端：点餐结算页](frontend/TASK.md)

## 本机提交服务

Mac 双击 **`启动提交服务.command`**，管理页会自动打开。需要 Python 3.10+。

1. 在管理页点“生成外网链接”，复制候选人链接发给他们。
2. 候选人用 Windows 或 macOS 的浏览器打开链接，填写姓名、题目和口令，上传 ZIP 代码包。无需安装提交客户端。
3. 你在管理页点“运行测试”，查看通过项、得分和完整日志。

本机已安装 `cloudflared` 时，也可双击 `start-public.command`，同时开启外网连接。终端按 Ctrl+C 停止；提交记录会保留。

验收使用仓库旁边的 **`agent-benchmark-private`** 面试官专用包，不上传 GitHub。只开页面和收文件无需 Node.js；前端自动验收需要提前安装专用包里的 Node 依赖和 Playwright 浏览器。

Windows 打包方式：右键完成的题目文件夹 → “压缩为 ZIP 文件”；Windows 10 选择“发送到 → 压缩(zipped)文件夹”。不要包含依赖目录。

[完整启动与使用说明](submission/README.md)
