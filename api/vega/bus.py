"""进程内事件总线：runner 回传的事件 → SSE 订阅者。

只服务「正在发生的流」，历史一律查库（重启不丢已定稿消息）。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, key: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs[key].append(q)
        return q

    def unsubscribe(self, key: int, q: asyncio.Queue) -> None:
        try:
            self._subs[key].remove(q)
        except ValueError:
            pass

    def publish(self, key: int, event: dict) -> None:
        for q in list(self._subs.get(key, [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # 订阅者消费不动就丢帧，SSE 有 DB 历史兜底
                pass


bus = EventBus()
