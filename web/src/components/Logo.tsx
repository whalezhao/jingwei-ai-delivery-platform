import type { CSSProperties } from "react";

/**
 * 经纬标识：墨黑方块里一个米白 V，一根金线横向穿过。
 * 左交点金线压 V、右交点 V 压金线——一上一下才是「织」，不是两条线交叉。
 */
const INK = "#12161C";
const CREAM = "#F4F1E8";
const GOLD = "#B8893B";

export function LogoMark({
  size = 24,
  simplified = false,
  className,
  style,
}: {
  size?: number;
  /** 16px 以下或 favicon：去掉织纹断口，只留 V + 金线。 */
  simplified?: boolean;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      role="img"
      aria-label="经纬"
      className={className ? `jw-mark ${className}` : "jw-mark"}
      style={style}
    >
      <rect width="24" height="24" rx="6" fill={INK} />
      {/* 深底上靠一圈极淡的边显出方块，浅底上不可见，无副作用 */}
      <rect
        x="0.5"
        y="0.5"
        width="23"
        height="23"
        rx="5.5"
        fill="none"
        stroke={CREAM}
        strokeOpacity="0.14"
        strokeWidth="0.5"
      />
      {simplified ? (
        <path
          d="M6.6 6.6 12 17.4 17.4 6.6"
          className="jw-line"
          pathLength={1}
          fill="none"
          stroke={CREAM}
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : (
        <>
          {/* 左斜线在金线处断开：金线在上 */}
          <path
            d="M6.6 6.6 9.3 12"
            className="jw-line"
            pathLength={1}
            style={{ animationDelay: "0.05s" }}
            stroke={CREAM}
            strokeWidth="2.2"
            strokeLinecap="round"
          />
          <path
            d="M10.5 14.4 12 17.4"
            className="jw-line"
            pathLength={1}
            style={{ animationDelay: "0.16s" }}
            stroke={CREAM}
            strokeWidth="2.2"
            strokeLinecap="round"
          />
          {/* 右斜线整根不断：V 在上 */}
          <path
            d="M12 17.4 17.4 6.6"
            className="jw-line"
            pathLength={1}
            style={{ animationDelay: "0.1s" }}
            stroke={CREAM}
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {/* 金线在右侧 V 处断开，让位给 V；两根往返如梭 */}
          <path
            d="M4.6 13.2h8.1"
            className="jw-weft-l"
            pathLength={1}
            stroke={GOLD}
            strokeWidth="2"
            strokeLinecap="round"
          />
          <path
            d="M15.5 13.2h3.9"
            className="jw-weft-r"
            pathLength={1}
            stroke={GOLD}
            strokeWidth="2"
            strokeLinecap="round"
          />
        </>
      )}
    </svg>
  );
}

export default function Logo({
  size = 22,
  onDark = false,
  showEn = true,
  style,
}: {
  size?: number;
  /** 墨色底上用米白字，色值随底翻转。 */
  onDark?: boolean;
  showEn?: boolean;
  style?: CSSProperties;
}) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: size * 0.34,
        whiteSpace: "nowrap",
        userSelect: "none",
        ...style,
      }}
    >
      <LogoMark size={size} />
      <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {showEn ? (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: size * 0.6,
              fontWeight: 600,
              lineHeight: 1,
              letterSpacing: "0.12em",
              color: onDark ? CREAM : "var(--ink)",
            }}
          >
            JINGWEI
          </span>
        ) : null}
        <span
          style={{
            fontSize: Math.max(9, size * 0.32),
            fontWeight: 500,
            lineHeight: 1.15,
            letterSpacing: "0.2em",
            color: onDark ? "rgba(244,241,232,0.52)" : "var(--ink-2)",
          }}
        >
          经纬
        </span>
      </span>
    </span>
  );
}