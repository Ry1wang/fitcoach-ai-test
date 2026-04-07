# 已知问题记录

---

## [ISSUE-001] 后端在处理大型 PDF 时因 OOM 重启，导致文档永久卡在 processing 状态

**发现日期：** 2026-04-03  
**影响范围：** `layer1_pre.py` 文档上传与索引流程  
**严重程度：** 高（阻断测试执行）  
**状态：** 已有临时规避方案，根本原因待修复

### 现象

运行 `layer1_pre.py` 时，文档上传成功（HTTP 202），但索引始终停留在 `processing` 状态，永不变为 `ready`。具体表现：

- 日志显示轮询等待超过 30～40 分钟无进展
- `docker inspect fitcoach-backend` 显示容器已重启多次（最多观察到 4 次）
- 重启后后端日志中**没有任何** pipeline 相关输出（无 ingestion、chunk、embed 日志）
- 受影响文档在数据库中永久保持 `processing` 状态，无任何 worker 继续处理

### 根本原因

后端使用 FastAPI `BackgroundTasks` 在 uvicorn 进程内执行文档索引（解析 → 分块 → 嵌入）。当多个大型 PDF 同时上传时，多个 background task 并发运行：

1. `chunk_document` 通过 `run_in_executor` 在线程池中执行，PyMuPDF + pdfplumber 会将整个 PDF 内容载入内存
2. 多个大文件（15～48 MB）同时处理，内存峰值超过容器限制（`docker-compose.yml` 中 backend 限制为 1G）
3. Docker OOM kill 终止容器，所有 in-flight 的 background task 被杀死
4. 容器重启后，数据库中这些文档的状态仍为 `processing`，但没有任何机制恢复或重试这些任务

**补充：** 后端 pipeline logger 的有效日志级别为 WARNING（`getEffectiveLevel()` = 30），INFO 级别的进度日志不会输出，导致问题难以直接从日志中发现。

### 诊断步骤

```bash
# 1. 确认容器重启次数
docker inspect fitcoach-backend --format '重启次数: {{.RestartCount}}'

# 2. 确认非 OOM（当前实例）—— 注意：重启后此值会重置为 false
docker inspect fitcoach-backend --format 'OOM killed: {{.State.OOMKilled}}'

# 3. 查看后端日志中是否有 pipeline 活动（正常应有 ingestion/chunk/embed 日志）
docker logs fitcoach-backend 2>&1 | grep -i "ingestion\|chunk\|embed\|pipeline\|doc_id"

# 4. 确认 logger 级别问题
docker exec fitcoach-backend python3 -c "
import logging
logger = logging.getLogger('app.services.pipeline')
print('effective level:', logging.getLevelName(logger.getEffectiveLevel()))
"
```

### 临时规避方案

**逐一上传文档，每个文档 ready 后再上传下一个。**

`layer1_pre.py` 已更新为此策略（`_wait_for_file_ready` 函数），避免多个 background task 并发占用大量内存。

若某个文档处理过程中后端恰好重启（偶发），文档会永久卡住。此时需手动清理：

```bash
# 获取 token
TOKEN=$(curl -s -X POST http://localhost/api/v1/auth/login \
  -d "username=test_runner@example.com&password=TestPassword123!" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 列出所有文档及状态
curl -s http://localhost/api/v1/documents \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
for d in json.load(sys.stdin).get('documents', []):
    print(f\"{d['id']}  {d['status']:<12}  {d['filename']}\")
"

# 删除指定卡住的文档（替换 <DOC_ID>）
curl -X DELETE http://localhost/api/v1/documents/<DOC_ID> \
  -H "Authorization: Bearer $TOKEN"
```

清理后重新运行 `layer1_pre.py` 即可。

### 推荐的根本修复方向

| 方案 | 说明 | 复杂度 |
|---|---|---|
| 提高内存限制 | 将 `docker-compose.yml` 中 backend 的 `memory: 1G` 改为 `memory: 2G` 或更高 | 低 |
| 启用 pipeline INFO 日志 | 在后端启动时配置 logging，使 `app.services.pipeline` 的 INFO 日志可见 | 低 |
| 孤儿任务恢复机制 | 后端启动时将所有 `processing` 状态文档重置为 `pending` 并重新触发索引 | 中 |
| 使用持久化任务队列 | 将 background task 迁移到 Celery + Redis 队列，支持任务持久化与重试，不依赖进程存活 | 高 |

---

## [ISSUE-002] 连续 Layer 3 测试运行触发后端 429 限流，导致 accuracy 虚低

**发现日期：** 2026-04-07  
**影响范围：** Layer 3 router accuracy 全量测试（105+ 条查询连续执行）  
**严重程度：** 高（导致测试结果不可信）  
**状态：** 已有规避方案，根本原因待后端修复

### 现象

Layer 3 全量测试第一次运行（105 条）约耗时 19 分钟，全部通过。
紧接着重新运行时，105 条查询中有 105 条立即返回 **HTTP 429 Too Many Requests**，
仅 15 条（Redis 缓存命中的 training 类查询）返回正常结果。
总耗时约 0.74 秒——看似飞速完成，实为大量请求被拒绝。

最终结果：
- accuracy = 15/105 = **14.3%**（远低于 90% 阈值，测试正确 FAIL）
- cross_domain acceptance_rate = 0/15 = **0%**

### 根本原因

后端对 LLM 调用没有针对测试场景的速率保护。连续 105 次查询耗尽了
底层 LLM API（或后端自身）的请求配额。限流窗口期内再次运行会立即失败。

附加发现：Redis 缓存使得部分相同查询在限流期间仍能命中缓存返回结果，
说明 Redis session cache 对相同 query 是共享的——存在跨请求 context 污染风险，
需要进一步确认缓存 key 设计是否安全。

### 规避方案

**在两次全量 Layer 3 测试之间等待足够时长（根据 LLM 提供商的限流窗口，通常为 60 秒到数分钟）。**

`run_layer3_tests.sh` 已可通过 `--smoke` 模式只运行 5 条核心查询（不触发限流）：

```bash
./scripts/run_layer3_tests.sh --smoke    # 只跑 5 条，约 1 分钟
./scripts/run_layer3_tests.sh            # 全量，需确保距上次运行有足够间隔
```

### 推荐的根本修复方向

| 方案 | 说明 | 复杂度 |
|---|---|---|
| 查询间加延迟 | 在 `routing_results` fixture 的每次请求之间加 `time.sleep(0.5~1s)` | 低（测试侧修复） |
| 后端提升 LLM 调用限流配额 | 联系 LLM 提供商提升 RPM/TPM 限额 | 低（配置变更） |
| 后端添加请求队列 | 对 LLM 调用做排队和限速，避免突发并发耗尽配额 | 中 |
| 测试侧使用独立 API key | 为测试环境配置独立的 LLM API key，与生产隔离 | 低 |

---

## [ISSUE-003] Agent degradation testing 的 LLM mock 场景无法从外部测试套件覆盖

**发现日期：** 2026-04-07  
**影响范围：** Layer 3 Agent degradation testing  
**严重程度：** 低（测试覆盖缺口，不影响生产功能）  
**状态：** 已知缺口，需后端单元测试补充

### 现象

TestPlan §5 Layer 3 要求测试每个 specialist agent 在以下 LLM 退化场景下的行为：
- LLM 返回空字符串
- LLM API 超时
- LLM 返回格式错误的 JSON

由于测试套件与应用代码分离（独立仓库），无法在测试执行时注入 mock LLM 响应。
`test_agent_degradation.py` 只能从 API 外部观测 agent 行为，无法直接触发以上场景。

### 已覆盖的替代测试

`router_accuracy/test_agent_degradation.py` 通过以下方式间接验证降级行为：
- 语料库外查询（out-of-corpus）：RAG 返回空 context，接近"LLM 无信息可用"场景
- SSE stream 格式验证：确认每个 agent 的响应结构符合规范
- 客户端中断（client abort）：验证服务器在连接意外关闭后能正常处理后续请求
- 近空查询：单字符/纯空格输入，不引发 5xx

### 推荐修复方向

在后端代码库（`fitcoach-ai`）中为每个 specialist agent 添加单元测试：

```python
# 示例：backend unit test (not in this repo)
from unittest.mock import AsyncMock, patch

async def test_training_agent_handles_empty_llm_response():
    with patch("app.agents.training.llm.ainvoke", return_value=AsyncMock(content="")):
        result = await training_agent.run("How do I squat?", context=[])
    assert result["answer"] == ""  # or a fallback message
    assert result["agent_used"] == "training"
    # Must NOT raise an unhandled exception

async def test_training_agent_handles_llm_timeout():
    with patch("app.agents.training.llm.ainvoke", side_effect=TimeoutError):
        result = await training_agent.run("How do I squat?", context=[])
    assert "error" in result or result["answer"]  # graceful fallback
```

---

## [ISSUE-004] 移动端(375px)布局溢出，发送按钮超出视口

**发现日期：** 2026-04-07  
**影响范围：** `e2e/tests/responsive.spec.ts` — mobile (375×812) viewport  
**严重程度：** 中（移动端用户无法直接点击发送按钮）  
**状态：** 已记录，待前端修复

### 现象

Playwright 在 375×812 视口下登录后，`button:has-text("发送")` 的 `boundingBox` 显示：

```
x + width = 561px  （超出视口宽度 375px）
```

侧边栏（文档管理）与聊天区域并排布局，两者宽度之和超过 375px，导致聊天输入行被整体推到视口右侧不可见区域。

**补充：** `scrollWidth > clientWidth` 检查未触发（CSS `overflow: hidden` 遮蔽了溢出），因此 "no horizontal scroll" 测试通过，但发送按钮实际无法点击。

### 推荐修复方向

在前端为侧边栏添加响应式断点（如 Tailwind `md:flex` + `hidden` on mobile），在窄屏下隐藏或折叠侧边栏，使聊天区域占满全屏。

### 测试处理

`responsive.spec.ts` 中该用例已标记 `test.fixme()`，不阻断 CI，直到前端完成响应式适配。

---

*此文件记录测试执行过程中发现的后端与测试脚本问题，供后续开发参考。*
