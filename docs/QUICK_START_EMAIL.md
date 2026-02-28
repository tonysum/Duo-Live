# 邮件报警功能快速开始指南

本指南帮助您快速配置和测试 duo-live 的邮件报警功能。

---

## 为什么需要邮件报警？

邮件报警与 Telegram 通知形成**双通道保障**，确保紧急情况（如平仓失败、保证金不足）能够及时触达您。

### 使用场景

- ✅ 平仓失败（完全失败或分批失败）
- ✅ 保证金不足
- ✅ 系统异常
- ✅ 每日交易报告（可选）

---

## 配置步骤

### 1. 获取163邮箱授权码

duo-live 默认使用163邮箱SMTP服务，您需要：

1. 登录 [163邮箱](https://mail.163.com)
2. 点击右上角"设置" → "POP3/SMTP/IMAP"
3. 开启"POP3/SMTP服务"或"IMAP/SMTP服务"
4. 点击"客户端授权密码"，按提示获取授权码
5. **保存授权码**（这不是您的邮箱密码！）

### 2. 配置环境变量

编辑项目根目录的 `.env` 文件，添加以下配置：

```bash
# 邮件报警配置
SMTP_EMAIL=your_email@163.com          # 您的163邮箱
SMTP_PASSWORD=your_authorization_code  # 刚才获取的授权码
ALERT_EMAIL=alert_receiver@example.com # 接收报警的邮箱（可以是任意邮箱）
```

**示例**:
```bash
SMTP_EMAIL=trading_bot@163.com
SMTP_PASSWORD=ABCDEFGHIJKLMNOP
ALERT_EMAIL=your_personal_email@gmail.com
```

### 3. 测试邮件功能

运行测试脚本验证配置是否正确：

```bash
python tests/test_email_alert.py
```

**预期输出**:
```
============================================================
duo-live 邮件报警功能测试
============================================================

📧 邮件配置检查通过
   发件邮箱: trading_bot@163.com
   收件邮箱: your_personal_email@gmail.com

📤 测试1: 发送普通邮件报警...
✅ 普通邮件发送成功

📤 测试2: 发送紧急报警邮件...
✅ 紧急报警邮件发送成功

🎉 所有测试通过！
📬 请检查您的邮箱 (your_personal_email@gmail.com) 是否收到测试邮件
```

### 4. 检查邮箱

打开您的接收邮箱（ALERT_EMAIL），应该收到2封测试邮件：

1. **测试邮件 - duo-live**
2. **紧急报警测试 - duo-live**

如果收到邮件，说明配置成功！

---

## 常见问题

### Q1: 测试失败，提示"535 Error: authentication failed"

**原因**: 授权码错误或未开启SMTP服务

**解决方案**:
1. 重新获取授权码，确保复制完整
2. 确认已开启"POP3/SMTP服务"或"IMAP/SMTP服务"
3. 检查 `.env` 文件中的 `SMTP_PASSWORD` 是否正确

### Q2: 测试失败，提示"Connection timeout"

**原因**: 网络问题或防火墙阻止

**解决方案**:
1. 检查服务器是否能访问 `smtp.163.com:465`
2. 检查防火墙规则
3. 尝试使用其他网络环境

### Q3: 收不到邮件，但测试显示成功

**原因**: 邮件被归类为垃圾邮件

**解决方案**:
1. 检查垃圾邮件文件夹
2. 将发件邮箱添加到白名单
3. 检查邮件过滤规则

### Q4: 想使用其他邮箱（如Gmail、QQ邮箱）

**解决方案**: 需要修改 `live/notifier.py` 中的SMTP配置

**Gmail 示例**:
```python
# 修改 send_email_alert() 方法中的这一行：
with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) as server:
```

**QQ邮箱示例**:
```python
with smtplib.SMTP_SSL('smtp.qq.com', 465, timeout=10) as server:
```

### Q5: 不想配置邮件，会影响系统运行吗？

**答**: 不会！邮件功能是可选的，不配置不影响系统正常运行。

---

## 实际使用

### 紧急报警示例

当平仓失败时，系统会自动发送紧急报警：

**Telegram 消息**:
```
🚨 平仓完全失败 - 紧急

ETHUSDT 所有平仓尝试都失败，请立即检查账户并手动平仓
建仓价格: 2500.00
持仓数量: 0.2
杠杆: 3x
最后错误: Margin is insufficient
```

**邮件内容**:
```
主题: [duo-live 交易系统] 平仓完全失败 - 紧急

duo-live 自动交易系统报警

时间: 2024-02-28 10:30:00

ETHUSDT 所有平仓尝试都失败，请立即检查账户并手动平仓
建仓价格: 2500.00
持仓数量: 0.2
杠杆: 3x
最后错误: Margin is insufficient

---
此邮件由 duo-live 交易系统自动发送
服务器: your-server-hostname
```

### 手动发送测试报警

在代码中使用：

```python
from live.notifier import TelegramNotifier

notifier = TelegramNotifier()

# 发送普通邮件
await notifier.send_email_alert(
    "测试主题",
    "测试内容"
)

# 发送紧急报警（Telegram + 邮件）
await notifier.send_critical_alert(
    "紧急报警",
    "这是一条紧急报警消息"
)
```

---

## 高级配置

### 自定义邮件模板

如果您想自定义邮件格式，可以修改 `live/notifier.py` 中的 `send_email_alert()` 方法：

```python
body = f"""
【您的自定义模板】

时间: {datetime.now()}
报警内容: {message}

---
duo-live 交易系统
"""
```

### 添加每日报告邮件

在 `live/trader.py` 的 `_daily_pnl_report()` 方法中添加：

```python
# 发送 Telegram 通知后，添加邮件报告
if self.notifier and self.notifier.email_enabled:
    await self.notifier.send_email_alert(
        "每日交易报告",
        f"余额: {bal['total_balance']}\n"
        f"今日盈亏: {daily_pnl}\n"
        f"持仓数: {open_count}"
    )
```

---

## 安全建议

1. **不要将 `.env` 文件提交到 Git**
   - 已在 `.gitignore` 中排除
   - 使用 `.env.example` 作为模板

2. **定期更换授权码**
   - 建议每3-6个月更换一次
   - 如果怀疑泄露，立即更换

3. **使用专用邮箱**
   - 建议创建一个专门用于交易系统的邮箱
   - 不要使用个人主邮箱

4. **限制接收邮箱**
   - ALERT_EMAIL 只设置为您信任的邮箱
   - 不要设置为公开邮箱

---

## 监控邮件发送状态

查看系统日志，搜索邮件相关日志：

```bash
# 查看邮件发送成功日志
grep "邮件报警已发送" logs/duo-live.log

# 查看邮件发送失败日志
grep "发送邮件报警失败" logs/duo-live.log
```

---

## 下一步

- ✅ 配置完成后，启动 duo-live 系统
- ✅ 系统会在紧急情况自动发送邮件
- ✅ 定期检查邮箱，确保能收到报警
- ✅ 如有问题，查看 [完整文档](improvements-from-ae-server.md)

---

**祝您交易顺利！** 📧🚀
