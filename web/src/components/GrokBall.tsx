/**
 * 侧栏底部的 AI 表情球。
 *
 * 引擎来自 https://github.com/tycoding/grok-ball （MIT），运行时放在 public/grok-ball.js，
 * 按需加载，避免打进首屏包。情绪由 ball.ts 总线驱动；悬停、失焦、久坐这里自己管。
 */

import { useEffect, useRef, useState } from "react";
import { flashBall, useBallEmotion } from "../ball";
import { useAnimatedSidebar } from "./motion/animated-sidebar";

type Engine = {
  setEmotion(id: string): boolean;
  setGaze(x: number, y: number): void;
  spin(): void;
  burst(count?: number): void;
  destroy(): void;
};

let loading: Promise<{ create: (el: Element, opts?: Record<string, unknown>) => Engine }> | null =
  null;

function loadEngine() {
  const w = window as any;
  if (w.GrokBall) return Promise.resolve(w.GrokBall);
  loading ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "/grok-ball.js";
    s.async = true;
    s.onload = () => (w.GrokBall ? resolve(w.GrokBall) : reject(new Error("GrokBall missing")));
    s.onerror = () => {
      loading = null; // 下次挂载再试
      reject(new Error("grok-ball load failed"));
    };
    document.head.appendChild(s);
  });
  return loading;
}

const IDLE = [
  [10 * 60_000, "00"],
  [3 * 60_000, "15"],
  [60_000, "04"],
] as const;

export default function GrokBall() {
  const { state } = useAnimatedSidebar();
  const emotion = useBallEmotion();
  const hostRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<Engine | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [blurred, setBlurred] = useState(false);
  const [idle, setIdle] = useState<string | null>(null);
  const hoverTimer = useRef<number | undefined>(undefined);

  const size = state === "collapsed" ? 36 : 112;
  const shown = hover ?? (blurred ? "06" : null) ?? idle ?? emotion;

  useEffect(() => {
    let dead = false;
    loadEngine()
      .then((Grok) => {
        if (dead || !hostRef.current) return;
        engineRef.current = Grok.create(hostRef.current, {
          emotion: "02",
          label: "AI",
          eyeScale: 1.15,
        });
        flashBall("05", 2500); // 加载苏醒
      })
      .catch(() => {});
    return () => {
      dead = true;
      engineRef.current?.destroy();
      engineRef.current = null;
    };
  }, []);

  useEffect(() => {
    engineRef.current?.setEmotion(shown);
  }, [shown]);

  // 眼神跟随光标
  useEffect(() => {
    const gaze = (e: PointerEvent) => {
      const box = hostRef.current?.getBoundingClientRect();
      if (!box || !engineRef.current) return;
      const clamp = (v: number) => Math.max(-1, Math.min(1, v));
      engineRef.current.setGaze(
        clamp((e.clientX - box.left - box.width / 2) / (box.width / 2)),
        clamp((e.clientY - box.top - box.height / 2) / (box.height / 2)),
      );
    };
    window.addEventListener("pointermove", gaze, { passive: true });
    return () => window.removeEventListener("pointermove", gaze);
  }, []);

  // 久无操作：04 发呆 → 15 疲惫 → 00 睡眠；有动作就唤醒回正常状态
  useEffect(() => {
    let last = Date.now();
    let wasIdle = false;
    const active = () => {
      last = Date.now();
      if (wasIdle) {
        wasIdle = false;
        setIdle(null);
        flashBall("01", 1800);
      }
    };
    const events: (keyof WindowEventMap)[] = ["pointermove", "keydown", "wheel", "touchstart"];
    events.forEach((n) => window.addEventListener(n, active, { passive: true }));
    const timer = window.setInterval(() => {
      const quiet = Date.now() - last;
      const next = IDLE.find(([ms]) => quiet >= ms)?.[1] ?? null;
      if (next) wasIdle = true;
      setIdle((cur) => (cur === next ? cur : next));
    }, 5000);
    return () => {
      events.forEach((n) => window.removeEventListener(n, active));
      window.clearInterval(timer);
    };
  }, []);

  // 切到别的窗口：06 休眠；回来 01 唤醒
  useEffect(() => {
    const blur = () => setBlurred(true);
    const focus = () => {
      setBlurred((was) => {
        if (was) flashBall("01", 1800);
        return false;
      });
    };
    window.addEventListener("blur", blur);
    window.addEventListener("focus", focus);
    return () => {
      window.removeEventListener("blur", blur);
      window.removeEventListener("focus", focus);
    };
  }, []);

  const enter = () => {
    setHover("03"); // 好奇
    window.clearTimeout(hoverTimer.current);
    hoverTimer.current = window.setTimeout(() => setHover("14"), 2000); // 盯久了害羞
  };
  const leave = () => {
    window.clearTimeout(hoverTimer.current);
    setHover(null);
  };
  const click = () => {
    window.clearTimeout(hoverTimer.current);
    setHover(null);
    flashBall("07", 2200); // 抖动唤醒
    engineRef.current?.spin();
    engineRef.current?.burst(12);
  };

  return (
    <div
      ref={hostRef}
      role="presentation"
      title="AI"
      onClick={click}
      onPointerEnter={enter}
      onPointerLeave={leave}
      style={{ width: size, height: size }}
      className="mx-auto cursor-pointer transition-[width,height] duration-300"
    />
  );
}
