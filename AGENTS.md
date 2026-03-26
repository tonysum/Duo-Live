# 面向 Agent / 协作者的开发说明

本文面向自动化助手与后续维护者：快速建立上下文、少踩实盘与部署坑。**业务背景以根目录 `README.md` 为准**；此处只写与改代码相关的“地图”和约束。

## 仓库结构（常用入口）

| 路径 | 说明 |
|------|------|
| `live/` | 实盘核心：策略、下单、监控、FastAPI（`api.py`）、`store`、配置读取 |
| `web/` | Vite + React 看板；环境变量 `VITE_API_URL`、`VITE_WS_TOKEN` 等 |
| `tests/` | `pytest` |
| `data/` | 运行时配置/数据（如 `config.json`）；勿把密钥提交进仓库 |
| `.cursor/commands/` | 斜杠命令：聊天里输入 **`/`** 可选中（如 **`git-push-github`**） |
| `.cursor/skills/` | Cursor 默认扫描的项目 Skill（如 `git-push-github/`） |
| `.agents/skills/` | 同上仓库内 Skill 的另一路径（与 `.cursor/skills` 可并存；改文案时请两处同步） |

## 本地命令

```bash
# Python（仓库根）
pytest

# 前端
cd web && npm run build && npm run lint
```

## 与 AI 协作时的硬约束

1. **实盘与资金**：改动交易路径前默认假设会影响真实资金；优先小步 diff、补测试或说明风险。
2. **时区与日志**：与扫描/展示时间相关时，以 **UTC** 与 README/代码中现有约定为准（PM2、`TZ` 等）。
3. **配置来源**：策略与 live 参数以 `data/config.json` 等为单一事实来源；避免再引入平行参数文件。
4. **API 形状**：前端 `web/src/lib/api.ts` 与后端 Pydantic 模型应保持字段一致；WS 负载见 `live/api.py` 的 `/ws/live`。

## Cursor 规则

项目级持久提示在 **`.cursor/rules/*.mdc`**（随仓库版本化）。修改惯例时同步更新对应 `.mdc`，不必在聊天里重复长说明。

## 扩展能力（Skill）

若需要领域化流程，新增 `SKILL.md` 时建议同时放在 **`.cursor/skills/<name>/`**（首选）与 **`.agents/skills/<name>/`** 各一份以便发现，或只保留一处并在这里写清单。推送 GitHub：对话里说「推到 GitHub」会走 Skill；或在输入框 **`/` → git-push-github** 一键注入同款说明。
