#!/usr/bin/env python3
"""基础功能全量验证(带韧性)+ HTTP 深待机唤醒可靠性测试。"""
import asyncio
import importlib.util
import os
import pathlib
import socket
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
    sys.exit("用法: python tools/verify_full.py <投影仪IP>  (或 export EPSON_HOST=<投影仪IP>)")
PORT = 8080
results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("✅" if ok else "❌"), name, ("— " + detail if detail else ""), flush=True)


def tcp_online():
    try:
        s = socket.create_connection((HOST, PORT), timeout=2)
        s.close()
        return True
    except OSError:
        return False


async def wait_online(max_s=60):
    w = 0.0
    while w < max_s:
        if tcp_online():
            return True
        await asyncio.sleep(3)
        w += 3
    return False


async def safe_probe(api):
    for _ in range(4):
        if not tcp_online():
            await wait_online()
        try:
            return await api.probe_state(verify=False)
        except mod.EpsonApiError:
            await asyncio.sleep(3)
    return "off", None


async def ensure_awake(api):
    st, _ = await safe_probe(api)
    if st == "on":
        return "on"
    if not tcp_online():
        await wait_online()
    try:
        await api.send_keyevent(26)
    except mod.EpsonApiError:
        pass
    ok = await api.wait_for_state("on", 12)
    if ok:
        return "on"
    st, _ = await safe_probe(api)
    return st


async def main():
    api = mod.EpsonApi(HOST, PORT)
    if not tcp_online():
        print("等待设备上线...", flush=True)
        await wait_online()
    await ensure_awake(api)

    print("== A. 唤醒状态基础功能 ==", flush=True)
    product = await api.get_product_name()
    record("A1 产品型号", product is not None, str(product))
    try:
        h, x = await api.get_light_time()
        record("A2 灯光时长", True, f"{h}h extra={x}")
    except mod.EpsonApiError as e:
        record("A2 灯光时长", False, str(e)[:40])
    smode = await api.get_standby_mode()
    record("A3 启动模式读取", smode is not None, f"raw={smode}")
    serial = await api.get_serial_number()
    record("A4 序列号读取", serial is not None, str(serial) if serial else "空(回退host:port)")
    try:
        await api.send_keyevent(3)
        record("A5 按键注入(HOME)", True)
    except mod.EpsonApiError as e:
        record("A5 按键注入(HOME)", False, str(e)[:40])
    try:
        await api.set_standby_mode(smode or "0")
        record("A6 启动模式写入(同值)", True)
    except mod.EpsonApiError as e:
        record("A6 启动模式写入(同值)", False, str(e)[:40])

    print("== B. HTTP 关机→HTTP 开机 循环 ==", flush=True)
    await api.send_keyevent(26)
    ok = await api.wait_for_state("off", 12)
    record("B1 HTTP 关机", ok)
    await asyncio.sleep(15)
    st, _ = await safe_probe(api)
    print("   待机确认:", st, flush=True)
    await api.send_keyevent(26)
    ok = await api.wait_for_state("on", 15)
    record("B2 HTTP 唤醒(深待机)", ok, "15s 内确认" if ok else "未确认")
    await api.close()

    ok_n = sum(1 for _, ok, _ in results if ok)
    print(f"\n结果: {ok_n}/{len(results)} 通过", flush=True)
    for name, ok, detail in results:
        if not ok:
            print("  ❌", name, "—", detail, flush=True)


asyncio.run(main())
