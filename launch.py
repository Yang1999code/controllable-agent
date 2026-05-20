"""launch.py — Web UI 启动器。由 start.bat 调用。"""
import socket
import webbrowser
import threading
import time
import urllib.request

from web_server import app
import uvicorn

PORT = 8765


def kill_stale_port():
    """如果端口已被占用，杀掉占用进程（Windows）。"""
    import subprocess
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", PORT))
        sock.close()
        if result != 0:
            return  # 端口空闲
        # 查找占用端口的进程
        out = subprocess.check_output(
            f'netstat -ano | findstr :{PORT}', shell=True, text=True
        )
        for line in out.strip().split("\n"):
            parts = line.strip().split()
            if len(parts) >= 5 and "LISTENING" in parts:
                pid = parts[-1]
                print(f"  发现旧进程 PID={pid} 占用端口 {PORT}，正在终止...")
                subprocess.run(
                    f"taskkill /F /PID {pid}", shell=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                time.sleep(0.5)
                print(f"  已清理 PID={pid}")
    except Exception:
        pass


def wait_and_open():
    """轮询等待服务器就绪，然后打开浏览器。"""
    url = f"http://127.0.0.1:{PORT}"
    for i in range(15):
        time.sleep(2)
        try:
            urllib.request.urlopen(f"{url}/status", timeout=2)
            print(f"\n  服务器已就绪! 正在打开浏览器...")
            webbrowser.open(url)
            return
        except Exception:
            print(f"  等待服务启动... ({i + 1}/15)")


if __name__ == "__main__":
    print("=" * 44)
    print("  my-agent - 多智能体协作框架")
    print(f"  http://127.0.0.1:{PORT}")
    print("=" * 44)
    print()

    kill_stale_port()
    threading.Thread(target=wait_and_open, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
