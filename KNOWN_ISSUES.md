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

*此文件记录测试执行过程中发现的后端与测试脚本问题，供后续开发参考。*
