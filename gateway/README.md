# 推理网关

[题目](TASK.md)。Python 3.10+，仅标准库。

复制 `backends.example.json` 为 `backends.local.json`，填写可用后端的 `base_url`（到 `/v1`）。需要密钥时增加 `"key_env":"BACKEND_A_KEY"` 并设置对应环境变量。

```text
python server.py --config backends.local.json
python -m unittest discover -s tests -v
python -m unittest discover -s acceptance -v
```

客户端地址：`http://127.0.0.1:8761/v1`，model填写后端支持的模型名。支持非流式文本消息。`GET /health` 检查网关，`GET /metrics` 查看路由状态。

联通检查：`python probe.py --config backends.local.json --model 实际模型名`，会发送真实模型请求。
