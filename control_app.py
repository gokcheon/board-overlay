"""조작앱 pywebview 래퍼 (인사해줘 2.0 스택) — 명세 3장 ③.

서버(main.py)가 떠 있는 상태에서 실행하면 /control 페이지를 창으로 감싼다.
같은 주소를 OBS 커스텀 독으로 등록해도 됨 (병행 사용 가능).
"""
import webview

from server.config import load_config

if __name__ == "__main__":
    cfg = load_config()
    port = int(cfg.get("port", 8140))
    webview.create_window(
        "보드게임 오버레이 조작앱",
        f"http://127.0.0.1:{port}/control",
        width=1620,
        height=980,
        min_size=(1200, 760),
    )
    webview.start()
