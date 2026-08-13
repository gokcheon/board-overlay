"""WebSocket 허브 — 연결된 오버레이/조작앱 전체로 상태·연출 이벤트 push."""
import json
from typing import Set

from fastapi import WebSocket


class WsHub:
    def __init__(self):
        self.clients: Set[WebSocket] = set()

    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def unregister(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, msg: dict) -> None:
        text = json.dumps(msg, ensure_ascii=False)
        dead = []
        for client in list(self.clients):
            try:
                await client.send_text(text)
            except Exception:
                dead.append(client)
        for client in dead:
            self.unregister(client)
