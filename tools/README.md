# 开发与调试工具

两个独立脚本，不依赖 Home Assistant，只需 Python 3.10+ 与 `aiohttp`。

## 指定投影仪地址

脚本通过第一个参数或 `EPSON_HOST` 环境变量指定投影仪 IP：

```bash
python tools/diagnose_state.py 192.168.1.100
# 或
export EPSON_HOST=192.168.1.100
python tools/diagnose_state.py
```

## 脚本

| 脚本 | 用途 |
|-|-|
| `diagnose_state.py` | 排查连接异常：逐条判别哪些命令会让固件的 HTTP 服务无响应，以及唤醒键是否有效 |
| `verify_full.py` | 真机验收：产品型号、灯光时长、启动模式、序列号、按键注入的读取与写入，以及深度待机下的唤醒可靠性 |

`verify_full.py` 会执行关机与开机操作。

## 注意

部分只读命令在特定电源状态下会让固件的 HTTP 服务失去响应，表现为 8080 端口超时。
出现这种情况通常需要重启投影仪恢复。`diagnose_state.py` 正是用来定位是哪一条命令
触发了这一状态。

`enum_map.json` 是固件命令枚举名与内部数值的映射清单，用于核对代码中使用的命令 id。
