import { useEffect, useState, type CSSProperties } from "react";

/**
 * 平台标识「Vega.」：字标 + 句点（宣言式，Ramp./Linear. 一脉）。句点是全站唯一强调点，
 * 蓝色取 var(--accent)。字母逐个 <i> 包裹以便入场动画按 --i 波浪错峰；无图形，缩放无损。
 * 句点后跟中文名「织女星」，字号随 size 同比缩放。
 */
const LETTERS = ["V", "e", "g", "a"];

export default function Logo({ size = 20, style }: { size?: number; style?: CSSProperties }) {
  // 入场动画每分钟重播一次：改 key 让 React 重挂载字母/句点，CSS animation 从头开始。
  // prefers-reduced-motion 下动画已由 CSS 关闭，无需另判。
  const [run, setRun] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setRun((n) => n + 1), 60_000);
    return () => clearInterval(t);
  }, []);

  return (
    <span className="vega-logo" style={{ fontSize: size, ...style }} role="img" aria-label="Vega 织女星">
      {LETTERS.map((ch, i) => (
        <i key={`${ch}-${run}`} style={{ "--i": i } as CSSProperties}>
          {ch}
        </i>
      ))}
      <b key={`dot-${run}`}>.</b>
      <span className="logo-cn">织女星</span>
    </span>
  );
}
