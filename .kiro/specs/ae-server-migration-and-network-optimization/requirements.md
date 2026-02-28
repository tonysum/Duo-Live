# 需求文档 - AE Server 迁移与网络优化

## 简介

本文档记录了从 AE Server Script 迁移到 duo-live 系统的核心交易逻辑改进，以及针对生产环境网络稳定性的优化需求。该功能包含两大类改进：

1. 四项核心交易逻辑改进（连续暴涨保护、平仓检查机制、分批平仓容错、邮件报警）
2. 网络稳定性优化（重试机制、超时配置、监控频率）

这些改进旨在提高交易系统的盈利能力、平仓成功率、系统稳定性和报警可靠性。

---

## 术语表

- **Trading_System**: 加密货币期货交易系统（duo-live）
- **Position_Monitor**: 持仓监控模块，负责监控和管理交易持仓
- **Strategy_Engine**: 策略引擎，负责评估持仓并调整止盈止损
- **Binance_Client**: 币安交易所API客户端
- **Notifier**: 通知模块，负责发送Telegram和邮件报警
- **Surge_Signal**: 卖量暴涨信号，指某小时的卖出量达到昨日平均的10倍以上
- **Consecutive_Surge**: 连续暴涨，指连续2小时都出现卖量暴涨
- **Position**: 持仓，包含交易对、数量、方向、开仓价格、止盈止损等信息
- **Force_Close**: 强制平仓，指通过市价单立即关闭持仓
- **Reduce_Only_Order**: 只减仓订单，币安期货的特殊订单类型，只能减少持仓不能增加
- **Algo_Order**: 算法订单，包括止盈止损等条件单
- **Margin_Insufficient**: 保证金不足错误，当账户保证金不足以支持当前操作时触发
- **SMTP**: 简单邮件传输协议，用于发送邮件
- **Network_Retry**: 网络重试机制，当API请求失败时自动重试
- **Backoff_Strategy**: 退避策略，重试时逐渐增加等待时间

---

## 需求

### 需求 1: 连续暴涨保护逻辑

**用户故事**: 作为交易员，我希望系统能识别连续暴涨信号并保持较高的止盈目标，以便充分捕获强势行情的盈利潜力。

#### 验收标准

1. WHEN Strategy_Engine 评估持仓达到12小时 AND 下跌占比小于60%, THE Strategy_Engine SHALL 检查是否为连续暴涨信号
2. THE Strategy_Engine SHALL 将连续暴涨定义为：信号小时和建仓小时的卖量都大于等于昨日平均小时卖量的10倍
3. WHEN 检测到连续暴涨 AND 持仓强度为强势币, THE Strategy_Engine SHALL 保持止盈目标为33%
4. WHEN 检测到连续暴涨 AND 持仓强度为中等币, THE Strategy_Engine SHALL 保持止盈目标为21%
5. WHEN 未检测到连续暴涨 AND 下跌占比小于60%, THE Strategy_Engine SHALL 将止盈目标降为10%（弱势币）
6. THE Strategy_Engine SHALL 通过查询币安API获取历史K线数据来计算卖量倍数
7. FOR ALL 连续暴涨判断, 解析历史K线数据然后计算卖量然后判断倍数 SHALL 产生与原始数据一致的结果（幂等性）

---

### 需求 2: 平仓前严格检查机制

**用户故事**: 作为交易员，我希望系统在平仓前进行严格检查，以便确保平仓操作使用准确的持仓信息并避免因未成交订单或精度问题导致的失败。

#### 验收标准

1. WHEN Position_Monitor 执行强制平仓, THE Position_Monitor SHALL 首先取消该交易对的所有未成交算法订单
2. WHEN 取消订单完成, THE Position_Monitor SHALL 从币安交易所获取实际持仓数量和方向
3. THE Position_Monitor SHALL 根据交易对的LOT_SIZE规则动态获取数量精度
4. THE Position_Monitor SHALL 将持仓数量调整为符合精度要求的值
5. WHEN 实际持仓数量大于0, THE Position_Monitor SHALL 使用SELL方向平仓（平多仓）
6. WHEN 实际持仓数量小于0, THE Position_Monitor SHALL 使用BUY方向平仓（平空仓）
7. THE Position_Monitor SHALL 首先尝试使用reduceOnly参数的市价单平仓
8. IF reduceOnly订单被拒绝, THEN THE Position_Monitor SHALL 使用普通市价单重试平仓
9. FOR ALL 平仓操作, 使用交易所实际持仓数据而非程序记录 SHALL 提高平仓成功率至95%以上

---

### 需求 3: 分批平仓容错机制

**用户故事**: 作为交易员，我希望系统在遇到保证金不足错误时能自动分批平仓，以便在极端情况下仍能成功关闭持仓。

#### 验收标准

1. WHEN Position_Monitor 执行平仓 AND 收到保证金不足错误, THE Position_Monitor SHALL 自动触发分批平仓流程
2. THE Position_Monitor SHALL 首先平仓当前持仓数量的50%
3. WHEN 第一批平仓完成, THE Position_Monitor SHALL 等待500毫秒
4. THE Position_Monitor SHALL 重新从交易所获取剩余持仓数量
5. THE Position_Monitor SHALL 平仓所有剩余持仓
6. IF 分批平仓仍然失败, THEN THE Position_Monitor SHALL 发送紧急报警通知
7. THE Position_Monitor SHALL 在日志中记录分批平仓的详细信息（原始数量、第一批数量、剩余数量）
8. FOR ALL 分批平仓操作, 第一批平仓然后等待然后第二批平仓 SHALL 产生与一次性平仓相同的最终持仓状态（零持仓）

---

### 需求 4: 邮件报警系统

**用户故事**: 作为交易员，我希望系统能通过邮件发送紧急报警，以便在Telegram不可用或未及时查看时仍能收到关键通知。

#### 验收标准

1. THE Notifier SHALL 支持通过SMTP协议发送邮件报警
2. THE Notifier SHALL 从环境变量读取SMTP服务器配置（邮箱地址、授权码、收件人地址）
3. THE Notifier SHALL 提供普通邮件报警方法，用于发送一般性通知
4. THE Notifier SHALL 提供紧急报警方法，同时发送Telegram消息和邮件
5. WHEN 平仓完全失败, THE Position_Monitor SHALL 调用紧急报警方法
6. WHEN 分批平仓仍然失败, THE Position_Monitor SHALL 调用紧急报警方法
7. THE Notifier SHALL 使用SSL加密连接到SMTP服务器（端口465）
8. WHERE 邮件配置未设置, THE Notifier SHALL 跳过邮件发送并记录警告日志
9. THE Notifier SHALL 在邮件发送失败时记录错误日志但不中断主流程
10. FOR ALL 邮件内容, 邮件主题和正文 SHALL 清晰描述报警类型和关键信息

---

### 需求 5: 网络重试机制优化

**用户故事**: 作为系统管理员，我希望系统能更好地处理网络波动，以便减少因临时网络问题导致的API请求失败。

#### 验收标准

1. THE Binance_Client SHALL 在API请求失败时自动重试最多5次
2. THE Binance_Client SHALL 使用指数退避策略，重试间隔依次为2秒、4秒、8秒、16秒、32秒
3. WHEN 网络请求失败, THE Binance_Client SHALL 记录警告日志包含端点路径和当前重试次数
4. WHEN 所有重试都失败, THE Binance_Client SHALL 抛出异常并记录错误日志
5. THE Binance_Client SHALL 对所有API端点应用统一的重试机制
6. FOR ALL 重试序列, 总等待时间 SHALL 为62秒（2+4+8+16+32）
7. FOR ALL 网络请求, 应用重试机制 SHALL 使请求成功率提高至少70%

---

### 需求 6: 超时配置优化

**用户故事**: 作为系统管理员，我希望系统能适应网络延迟较高的环境，以便避免因超时过短导致的请求失败。

#### 验收标准

1. THE Binance_Client SHALL 使用60秒作为默认HTTP请求超时时间
2. THE Binance_Client SHALL 允许在初始化时自定义超时时间
3. WHEN HTTP请求超过超时时间, THE Binance_Client SHALL 取消请求并触发重试机制
4. THE Binance_Client SHALL 对所有API请求应用统一的超时配置

---

### 需求 7: 监控频率优化

**用户故事**: 作为系统管理员，我希望系统能降低API请求频率，以便减少触发币安API限流的风险并降低网络负载。

#### 验收标准

1. THE Position_Monitor SHALL 每60秒执行一次持仓检查循环
2. THE Position_Monitor SHALL 在配置文件中定义监控间隔参数
3. THE Position_Monitor SHALL 在每次循环开始时记录日志
4. WHEN 监控间隔时间未到, THE Position_Monitor SHALL 等待直到下一个检查周期
5. FOR ALL 监控周期, 降低频率至60秒 SHALL 使API请求总量减少50%

---

### 需求 8: 配置管理

**用户故事**: 作为系统管理员，我希望所有新增配置都有清晰的文档和示例，以便快速完成系统部署和配置。

#### 验收标准

1. THE Trading_System SHALL 在环境变量示例文件中包含所有邮件配置参数
2. THE Trading_System SHALL 提供邮件配置快速指南文档
3. THE Trading_System SHALL 提供邮件功能测试脚本
4. THE Trading_System SHALL 在README文档中说明所有新增功能和配置要求
5. WHERE 邮件配置缺失, THE Trading_System SHALL 正常运行但跳过邮件报警功能
6. THE Trading_System SHALL 在启动时验证必需的环境变量（币安API密钥、Telegram配置）
7. THE Trading_System SHALL 在启动时记录可选配置的状态（邮件功能是否启用）

---

## 正确性属性

### 属性 1: 连续暴涨判断的幂等性
FOR ALL 持仓和K线数据，多次执行连续暴涨检查 SHALL 返回相同的结果

### 属性 2: 平仓操作的完整性
FOR ALL 强制平仓操作，平仓完成后交易所的实际持仓数量 SHALL 为0

### 属性 3: 分批平仓的等价性
FOR ALL 分批平仓操作，最终持仓状态 SHALL 与一次性平仓的最终状态相同（零持仓）

### 属性 4: 重试机制的单调性
FOR ALL 网络请求，重试次数增加 SHALL 使请求成功率单调递增或保持不变

### 属性 5: 配置的向后兼容性
FOR ALL 新增配置参数，缺失配置 SHALL 不影响系统的核心交易功能

### 属性 6: 通知的可靠性
FOR ALL 紧急报警，至少一个通知渠道（Telegram或邮件）成功发送 SHALL 使报警被记录为已发送

### 属性 7: 精度调整的正确性
FOR ALL 持仓数量，精度调整后的数量 SHALL 符合交易所的LOT_SIZE规则且不大于原始数量

### 属性 8: 订单取消的完整性
FOR ALL 平仓前的订单取消操作，所有未成交算法订单 SHALL 被成功取消或已不存在

---

## 非功能性需求

### 性能要求

1. THE Strategy_Engine SHALL 在5秒内完成单个持仓的连续暴涨检查
2. THE Position_Monitor SHALL 在30秒内完成单次平仓操作（不含重试）
3. THE Notifier SHALL 在10秒内完成单封邮件的发送

### 可靠性要求

1. THE Trading_System SHALL 在网络错误率降低70%后保持稳定运行
2. THE Position_Monitor SHALL 实现95%以上的平仓成功率
3. THE Notifier SHALL 实现90%以上的邮件发送成功率

### 可维护性要求

1. THE Trading_System SHALL 提供详细的日志记录所有关键操作
2. THE Trading_System SHALL 提供测试脚本验证邮件功能
3. THE Trading_System SHALL 提供文档说明所有新增功能和配置

### 安全性要求

1. THE Trading_System SHALL 通过环境变量管理敏感配置（API密钥、邮箱密码）
2. THE Notifier SHALL 使用SSL加密连接到SMTP服务器
3. THE Trading_System SHALL 不在日志中记录完整的API密钥或密码

---

## 依赖关系

- 币安期货API（用于获取持仓、K线数据、下单、取消订单）
- SMTP邮件服务器（推荐163邮箱）
- Telegram Bot API（用于发送Telegram通知）
- Python 3.8+
- aiohttp库（用于异步HTTP请求）
- aiosmtplib库（用于异步SMTP邮件发送）

---

## 约束条件

1. 连续暴涨检查需要访问历史K线数据，受币安API限流限制
2. 邮件发送受SMTP服务器限流限制（163邮箱：每天最多发送50封）
3. 分批平仓需要等待第一批订单执行，可能延长总平仓时间
4. 网络重试机制会增加API请求的总耗时（最多62秒）
5. 监控频率降低会延迟持仓状态的检测（最多60秒）

---

## 验证方法

### 单元测试
- 连续暴涨判断逻辑的单元测试（模拟K线数据）
- 精度调整算法的单元测试
- 邮件发送功能的单元测试

### 集成测试
- 平仓流程的端到端测试（使用测试网）
- 分批平仓容错的集成测试
- 网络重试机制的集成测试

### 手动测试
- 邮件报警功能测试（使用提供的测试脚本）
- 连续暴涨保护的实盘验证
- 网络优化效果的监控和统计

---

## 版本信息

- **文档版本**: 1.0
- **创建日期**: 2024-02-28
- **功能版本**: duo-live v2.0
- **工作流类型**: requirements-first
- **规范类型**: feature

