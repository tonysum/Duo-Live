---
name: git-push-github
description: >-
  Stages all changes, commits with a short message, and pushes to GitHub (origin).
  Use when the user wants to upload code after edits, sync to GitHub, run add+commit+push
  in one go, 快速上传, 推到 GitHub, or skip typing the three git steps manually.
---

> 与 `.agents/skills/git-push-github/SKILL.md` 为同一技能；改流程时请两处同步。

# Git 三步：add → commit → push

## 何时使用

用户表达类似意图时执行本技能：**改完代码要上 GitHub**、**快速提交推送**、**执行 git 三步**、**upload / push to GitHub**。

## Agent 执行顺序

1. **`git status`**（必要时 **`git diff --stat`**）确认有改动；若工作区干净，告知用户并**停止**。
2. **提交说明**：若用户未给，根据 `diff` 写**一行**简明 `commit message`（可读性强，避免「update」空洞）；用户已给则用其原文。
3. **安全提醒**：推送前快速扫一眼是否误含密钥、本地私钥、`data/` 下不该提交的敏感文件；若发现，先让用户处理再推送。
4. 在仓库根目录执行（路径以用户工作区为准，本仓库一般为 `duo-live` 根）：

   ```bash
   git add -A
   git commit -m "<message>"
   git push
   ```

5. **权限**：`commit` 需要 **git_write**；`push` 需要 **network**。
6. **失败处理**：
   - `commit` 失败（如 hook、合并冲突）：**不要** `--no-verify` 除非用户明确要求；贴出错误让用户处理。
   - **无上游分支**：`git push -u origin "$(git branch --show-current)"`。
   - **切勿** `git push --force` 除非用户**明确**要求。

## 可选：用户自己在终端一键跑

仓库内脚本（不经过 Agent 时）：

```bash
./scripts/git-quick-push.sh "你的提交说明"
```

未传说明时默认 `update`（仍建议写清楚）。
