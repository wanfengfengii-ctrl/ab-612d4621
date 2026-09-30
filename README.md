# 遥测帧比特滑移复原服务

卫星地面站接收连续遥测时，链路偶发**插入**或**漏失**单个比特。本服务在
**整条接收比特流上联合解释**所有插入与漏失，恢复由若干固定结构帧组成的
完整帧流，避免按同步字逐段截取时把载荷中的伪同步字误判为帧首。

每帧结构：

```
sync_word(6–12 bit) | payload(16–48 bit) | CRC-8(8 bit)
```

- CRC 多项式 x⁸+x²+x+1（系数字节 0x07），最高位优先、初值零、无反射无异或；
  校验值为 `(同步字 ‖ 载荷)` 后补八个零再做多项式取余。
- 帧数 3–8，滑移（插入/漏失）预算最多 6。

## 复原准则（按优先级）

1. **滑移次数最少**；
2. 滑移次数相同时，取**校正比特串字典序最小**者（`"0" < "1"`）；
3. 报告最优解是否**唯一**（是否存在其他同代价校正串）。

只在存在能解释整条流的完整校正串时输出；预算内无解时返回
**不可复原**结论与已验证的最小所需滑移下界，绝不返回局部帧或猜测载荷。

## 算法

分层动态规划（按滑移代价分层），状态仅含结构量
`(已完成帧, 帧内位置, CRC 运行寄存器, 输入/校正偏移)`：

- 同步字头阶段强制逐位匹配同步字；载荷位驱动 CRC 寄存器；CRC 阶段的 8 位
  由寄存器终值**强制**，不匹配即终止 —— 因此载荷中出现同步字永远不会被
  当成帧首，且状态可达性只依赖结构量；
- 零代价“匹配”在每层内取闭包，插入/漏失代价为 1 并开启下一层；
- 前向可达集给出最小滑移数；反向可达集支撑贪心重构字典序最小校正串，
  并在某个校正位置 `0/1` 均可完成时判定非唯一；
- 校正串确定后用小规模编辑 DP 求规范（字典序最小）的滑移位置标注。

纯 Python 标准库实现，无第三方依赖。

## HTTP API

监听端口由环境变量 `TELEMETRY_PORT` 配置（默认 8080）。

### `GET /healthz`

健康检查，返回 `200 {"status":"ok"}`。

### `POST /api/v1/recover`

请求：

```json
{
  "received_bits": "101101……",
  "frame_count": 4,
  "sync_word": "10110011",
  "payload_length": 24,
  "max_slips": 6
}
```

`max_slips` 可省略，默认 6。

成功（200）：

```json
{
  "status": "recovered",
  "recoverable": true,
  "min_slips": 1,
  "unique": true,
  "other_equal_cost_corrections": false,
  "corrected_bits": "……",
  "corrected_length": 160,
  "frames": [
    {"index": 1, "sync_word": "……", "payload": "……", "crc": "……",
     "crc_valid": true}
  ],
  "slips": [
    {"type": "insertion", "index": 30, "stream": "received", "bit": "1",
     "corrected_index": 30},
    {"type": "deletion",  "index": 33, "stream": "corrected", "bit": "0",
     "received_index": 33}
  ]
}
```

- `insertion`：链路多传的比特，`index` 为其在接收串中的 0 基位置；
- `deletion`：链路漏掉的比特，`index` 为其在校正（原始）串中的 0 基位置。
  同一位处于等值游程时存在多个等价对齐，此时报告字典序最小的规范位置。

不可复原（200）：

```json
{
  "status": "unrecoverable",
  "recoverable": false,
  "lower_bound_slips": 7,
  "lower_bound_tight": false,
  "searched_slips": 6,
  "reason": "no frame-consistent correction within 6 slips; ..."
}
```

服务还会在预算之外有限地加大搜索（用于把下界收紧到某个确切值），此时
`lower_bound_tight` 为 `true`。响应不含任何 `frames` / `corrected_bits`。

输入不合法返回 `422`，逐字段给出错误：

```json
{"error": "validation_failed",
 "fields": [{"field": "frame_count", "message": "must be between 3 and 8"}]}
```

非法 JSON 返回 `400`。

## 运行

```bash
# 启动 API（带健康检查）
TELEMETRY_PORT=8080 docker compose up --build api

# 运行一次性校验服务：构建检查 + 单元测试 + 含伪同步字的复原冒烟，
# 随后自行退出，退出码为位掩码：
#   0 全部通过；1 代码测试失败；2 构建检查失败；4 复原冒烟失败
docker compose build api verify
docker compose up verify          # 或: docker compose run verify
echo $?
```

不使用容器时：

```bash
python3 verify.py          # 全部校验（内部临时启动 API 做冒烟）
TELEMETRY_PORT=8080 python3 -m app.server
python3 smoke.py           # 对已运行的 API 做冒烟
python3 -m unittest discover -s tests -t .
```

## 目录结构

```
app/crc.py         CRC-8（含增量寄存器接口）
app/recovery.py    联合复原分层 DP、字典序择优、唯一性、下界
app/validation.py  逐字段请求校验
app/server.py      stdlib HTTP API（/healthz、/api/v1/recover）
tests/             CRC、复原、校验、HTTP 测试
smoke.py           伪同步字 + 滑移端到端冒烟
verify.py          一次性校验，退出码位汇总
Dockerfile / docker-compose.yml
```
