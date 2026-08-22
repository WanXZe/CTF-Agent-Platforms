# 本地 Mock 靶场平台

仿西湖论剑 slab-match API 的本地 Mock 服务器，用于开发调试。

## 启动 Mock 服务器

```bash
cd ../../mock-platform
python mock_server.py --port 8080 --access-key test-key-123
```

## 配置

默认指向 `http://localhost:8080`，AccessKey 为 `test-key-123`。
可在 `config.yaml` 中修改。
