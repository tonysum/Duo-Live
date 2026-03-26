# 推送到 GitHub（add + commit + push）

请在本仓库根目录执行 **git 三步**（与技能 `git-push-github` 一致）：

1. `git status`，必要时 `git diff --stat`；工作区干净则直接说明并结束。
2. 若本对话里用户已经写了提交说明，**必须**用那句话作为 `git commit -m`；否则根据当前 diff 写**一行**简明、可读的提交信息（避免空洞的 `update`）。
3. 推送前若发现疑似密钥、私钥或不应提交的敏感文件在变更里，先提醒用户、**不要**提交。
4. 执行：`git add -A` → `git commit -m "<message>"` → `git push`。首次推当前分支时用 `git push -u origin "$(git branch --show-current)"`。
5. 需要 **git_write** 与 **network**；**禁止** `git push --force`（除非用户本条里明确要求）。

完成后简要汇报：分支名、提交 hash 前缀（若有）、远程是否成功。
