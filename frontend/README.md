# 前端题启动

[题目](TASK.md)。Python 3.10+与浏览器。

运行 `python serve.py`，打开 http://127.0.0.1:8763 。

自动测试需要Node.js 20+，依赖提前安装：

```text
npm ci
npx playwright install chromium
npm test
```

测试会自行启动服务，运行前关闭手动启动的8763端口服务。
