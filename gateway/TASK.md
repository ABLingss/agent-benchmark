# 题一：让真实推理网关支持缓存亲和路由

时间20分钟，使用自己的 Agent。起点是已有真实HTTP转发、轮询、容量预留和错误处理的网关。完成 `cache_aware` 策略并保留已有行为。

## 必须实现

1. 请求头 `X-Routing-Policy: cache_aware` 启用新策略；省略该头或指定 `round_robin` 时行为不变。未知策略400 `invalid_policy`。
2. 只选 `healthy=True` 且 `in_flight < capacity` 的后端。比较请求token与各后端所有历史前缀的最长公共前缀，至少匹配2个token才有亲和性。
3. 有亲和性：最长前缀优先；并列按当前 `in_flight/capacity` 最低者；再并列按 id 字典序最小者。
4. 无亲和性：沿用原轮询；亲和命中不移动轮询游标。无可用后端503 `no_backend`。
5. 继续真实转发请求。上游响应状态、正文、Content-Type保留；不得生成假回答、返回固定结果或把失败改成成功。2xx响应后记入前缀，其他状态或异常不记；所有请求结束后释放容量。
6. 并发时选择和占容量必须原子执行，不超卖容量。旧参数校验、错误格式和轮询测试继续通过。

## 约定

`router.py` 的 `prompt_tokens()` 是本题固定的调度分词规则：每条消息按 `role + 空格 + content` 拼接，按空白分词，区分大小写。不是模型BPE，不能替换。`prefixes` 只记录真实请求成功后的调度观察，不能证明后端真实KV命中；不要求实现KV张量或推理引擎。

`Router.reserve(tokens, policy)` 返回后端和亲和长度；`release()` 负责成功记录与释放。可以调整实现，但保留公开接口。

交付：代码、实际测试结果、简述关键改动和已知限制。不得修改面试方公开测试来获得通过；可增加自己的测试。

原功能检查：`python -m unittest discover -s tests -v`

新功能公开验收：`python -m unittest discover -s acceptance -v`（初始失败是预期。）

不要求另写界面、接入MCP或多Agent，也不按代码量给分。
