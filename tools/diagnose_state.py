#!/usr/bin/env python3
"""系统性判别:哪个命令挂起服务器、KEYEVENT 26 能否唤醒。"""
import asyncio
import importlib.util
import os
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "epson_ef"
pkg = types.ModuleType("epson_ef")
pkg.__path__ = [str(ROOT)]
sys.modules["epson_ef"] = pkg
spec = importlib.util.spec_from_file_location("epson_ef.api", str(ROOT / "api.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["epson_ef.api"] = mod
spec.loader.exec_module(mod)

HOST = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EPSON_HOST", "")
if not HOST:
    sys.exit("用法: python tools/diagnose_state.py <投影仪IP>  (或 export EPSON_HOST=<投影仪IP>)")


async def attempt(api, cmd, params=None, timeout=6):
    try:
        data = await asyncio.wait_for(api.request(cmd, params=params), timeout=timeout)
        return f"200 {str(data.get('response'))[:40]!r}"
    except asyncio.TimeoutError:
        return "TIMEOUT(挂起>6s)"
    except mod.EpsonApiCommandFailed:
        return "FAIL(优雅)"
    except mod.EpsonApiUnavailable as e:
        return f"UNAVAIL:{str(e)[:40]}"
    except mod.EpsonApiError as e:
        return f"ERR:{str(e)[:40]}"


async def main():
    api = mod.EpsonApi(HOST, 8080)
    tests = [
        ("PRODUCT_NAME", "CONFIG_GET_PRODUCT_NAME", None),
        ("LIGHT_TIME", "CONFIG_GET_LIGHT_TIME", None),
        ("SALES_AREA", "CONFIG_GET_SALES_AREA", None),
        ("PJ_STANDBY_MODE", "CONFIG_GET_PJ_STANDBY_MODE", None),
        ("KEYEVENT 26(开机)", "NOTIFY_KEYEVENT", "26"),
    ]
    for label, cmd, params in tests:
        r = await attempt(api, cmd, params)
        print(f"{label}: {r}")
        await asyncio.sleep(1)

    # 等待+复查
    await asyncio.sleep(8)
    st, _ = await api.probe_state(verify=False)
    print("当前状态:", st)
    await api.close()


asyncio.run(main())
