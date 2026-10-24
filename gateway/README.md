# 真实HTTP推理网关

Python 3.10+，仅标准库。没有模拟推理或内置假模型；转发至你配置的真实兼容服务。只支持非流式、文本消息的 `/v1/chat/completions`。

在本目录先运行 `python -m unittest discover -s tests -v`。正式考核前由面试官配置并检查真实后端，避免安装模型占用20分钟。

复制 `backends.example.json` 为 `backends.local.json`，填写真实服务地址。`base_url` 填到 `/v1`，不要包含 `/chat/completions`。示例里的8001/8002不是随包提供的服务，必须换成已运行的真实服务。

如果后端要求密钥，在该项增加 `"key_env":"BACKEND_A_KEY"`，在本机设置该环境变量。不要把密钥写进配置或Git。远程地址要求HTTPS，本地地址可以HTTP。启动：

```text
python server.py --config backends.local.json
```

考前真实联通检查：`python probe.py --config backends.local.json --model 实际模型名`。这会向每个配置后端发送一次小请求，消耗真实模型额度；不输出密钥或回答正文。未配置真实后端前，不应宣称已经完成模型联通。

把原来模型客户端的地址改成 `http://127.0.0.1:8761/v1`，model使用后端实际支持的模型名。原来的 messages、temperature、max_tokens 等字段会转发。默认轮询；完成题目后在客户端请求头增加 `X-Routing-Policy: cache_aware`。

`GET /health` 只检查网关存活；`GET /metrics` 查看容量与前缀观察数量。它不代表上游健康。服务仅监听本机，未提供面向公网的入站鉴权。

测试中本地HTTP接收端用于核验真实网络收发、字节正文和状态码，不提供模型推理，也不能替代真实模型的考前联通检查。网关可执行但本仓库不附模型或密钥。
