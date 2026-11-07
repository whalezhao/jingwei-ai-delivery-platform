import type { ReactNode } from "react";
import { Check, ChevronDown, CircleStop, FileText, LoaderCircle, X } from "lucide-react";

/**
 * 思考链（替代旧 ThoughtChain）：一行一个过程节点，可折叠节点点开看
 * 正文；status 压过 icon（转圈 / ✓ / ✗ / ⊖），blink 表示正在流式输出。
 */
export type ChainStatus = "loading" | "success" | "error" | "abort";

export interface ChainItem {
  key: string;
  title?: ReactNode;
  description?: ReactNode;
  content?: ReactNode;
  footer?: ReactNode;
  icon?: ReactNode;
  status?: ChainStatus;
  collapsible?: boolean;
  blink?: boolean;
}

const STATUS_ICON: Record<ChainStatus, ReactNode> = {
  loading: <LoaderCircle size={14} className="animate-spin" />,
  success: <Check size={14} />,
  error: <X size={14} />,
  abort: <CircleStop size={14} />,
};

export function Chain({
  items,
  expandedKeys = [],
  onExpand,
}: {
  items: ChainItem[];
  expandedKeys?: string[];
  onExpand?: (keys: string[]) => void;
}) {
  return (
    <div className="tc-chain">
      {items.map((it) => {
        const open = expandedKeys.includes(it.key);
        const collapsible = !!it.collapsible && it.content != null;
        const toggle = () => {
          if (!collapsible) return;
          onExpand?.(open ? expandedKeys.filter((k) => k !== it.key) : [...expandedKeys, it.key]);
        };
        return (
          <div className="tc-chain-node" key={it.key}>
            <span className={`tc-chain-icon tc-st-${it.status ?? "idle"}`}>
              {it.status ? STATUS_ICON[it.status] : (it.icon ?? <span className="tc-chain-dot" />)}
            </span>
            <div className="tc-chain-main">
              <div
                role={collapsible ? "button" : undefined}
                tabIndex={collapsible ? 0 : undefined}
                aria-expanded={collapsible ? open : undefined}
                className={`tc-chain-head${collapsible ? " is-collapsible" : ""}`}
                onClick={toggle}
                onKeyDown={(e) => {
                  if (!collapsible) return;
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggle();
                  }
                }}
              >
                <span className={`tc-chain-title${it.blink ? " blink" : ""}`}>{it.title}</span>
                {collapsible && <ChevronDown size={13} className="tc-chain-caret" />}
              </div>
              {it.description != null && it.description !== "" && (
                <div className="tc-chain-desc">{it.description}</div>
              )}
              {collapsible && open && <div className="tc-chain-content">{it.content}</div>}
              {it.footer && <div className="tc-chain-footer">{it.footer}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** 文件卡（替代旧 FileCard）：文件名 + 版本/定稿说明。 */
export function FileChip({
  name,
  description,
  onClick,
}: {
  name: ReactNode;
  description?: ReactNode;
  onClick?: () => void;
}) {
  const title = typeof name === "string" ? name : undefined;
  const inner = (
    <>
      <FileText size={14} className="rc-fchip-ico" />
      <span className="rc-fchip-name">{name}</span>
      {description != null && description !== "" && <span className="rc-fchip-desc">{description}</span>}
    </>
  );
  return onClick ? (
    <button type="button" className="rc-fchip" onClick={onClick} title={title}>
      {inner}
    </button>
  ) : (
    <span className="rc-fchip">{inner}</span>
  );
}

/** 记忆来源 chips（替代旧 Sources 的 inline 形态）。 */
export function SourceChips({
  items,
}: {
  items: { key: string; title: ReactNode; description?: ReactNode }[];
}) {
  return (
    <span className="rc-srcchips">
      {items.map((s) => (
        <span
          key={s.key}
          className="rc-srcchip"
          title={typeof s.title === "string" ? s.title : undefined}
        >
          {s.title}
          {s.description ? <em>{s.description}</em> : null}
        </span>
      ))}
    </span>
  );
}