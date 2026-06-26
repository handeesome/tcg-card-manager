#!/usr/bin/env python3
"""
一键 MITM 代理脚本
在 PC 上启动 mitmproxy，证书文件生成好，用户只需传进 MuMu 即可
"""
import subprocess, sys, shutil, os
from pathlib import Path

CERT_NAME = "c8750f0d.0"  # 集换社 Android 需要的系统证书哈希名

def main():
    cert_dir = Path.home() / ".mitmproxy"
    cert_src = cert_dir / "mitmproxy-ca-cert.pem"

    if not cert_src.exists():
        print("❌ 找不到证书，请先运行一次 mitmproxy 生成证书")
        sys.exit(1)

    # 目标文件
    out_dir = Path(r"D:\my-projects\卡牌交易助手\scripts\proxy_setup")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 复制证书并改成 Android 系统需要的文件名
    dest = out_dir / CERT_NAME
    shutil.copy(cert_src, dest)
    print(f"✅ 证书已生成: {dest}")
    print(f"   Android 系统文件名: {CERT_NAME}")
    print()

    # 打印操作指南
    print("=" * 50)
    print("MuMu 操作步骤:")
    print("=" * 50)
    print()
    print("1. MuMu 设置 → WLAN → 长按WiFi → 修改网络")
    print("   代理手动 → 192.168.31.62:9999")
    print()
    print("2. 把 proxy_setup/c8750f0d.0 文件拖到 MuMu 共享文件夹")
    print()
    print("3. MuMu 里用 MT管理器:")
    print("   左边打开 /sdcard/MuMuShare/ 找到 c8750f0d.0")
    print("   右边打开 /system/etc/security/cacerts/")
    print("   长按左边文件 → 复制 → 右边粘贴")
    print("   如果粘贴失败，点MT管理器右上角'挂载读写'")
    print()
    print("4. 重启 MuMu → 打开集换社 → 搜'皮卡丘' → 看详情页")
    print()
    print("5. PC 浏览器打开 http://localhost:8081 看抓到的请求")
    print()

    # 尝试启动 mitmdump
    try:
        mitmdump = Path.home() / "AppData/Local/hermes/hermes-agent/venv/Scripts/mitmdump.exe"
        if mitmdump.exists():
            print("正在启动 mitmdump (端口 9999)...")
            subprocess.Popen([str(mitmdump), "--listen-port", "9999", "--set", "block_global=false"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("✅ mitmdump 已启动")
    except:
        print("⚠️ 请手动启动 mitmdump --listen-port 9999")

    print()
    print("一切就绪！去 MuMu 操作吧。")

if __name__ == "__main__":
    main()
