import { useCallback, useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { toast } from "../ui/toast";
import SearchSelect from "../ui/SearchSelect";
import { Input } from "./motion/input";

interface AmItem {
  id: string;
  name: string;
  description: string;
}

/**
 * agent-memory 实时下拉：展开现场拉列表（团队 / 指定团队的专家）。
 * hub 拉取失败时降级为手输框，不阻塞配置；已保存但不在列表里的值原样回显。
 */
export default function AmSelect({
  kind,
  teamId,
  placeholder,
  value,
  onChange,
  onLabelChange,
  size,
  style,
  prefix,
}: {
  kind: "team" | "agent";
  teamId?: string; // kind=agent 时生效
  placeholder?: string;
  value?: string | null;
  onChange?: (v: string | undefined) => void;
  // 选项页要在按钮上显示选中的名字：值可能是历史回放（非点选），只能由列表解析
  onLabelChange?: (name: string | undefined) => void;
  size?: "small" | "middle" | "large";
  style?: CSSProperties;
  prefix?: ReactNode;
  // beUI 下拉 portal 到 body，无需 antd 的挂载容器；保留入参兼容旧调用
  getPopupContainer?: (triggerNode: HTMLElement) => HTMLElement;
}) {
  const { t } = useTranslation();
  const [options, setOptions] = useState<AmItem[]>([]);
  const [fallback, setFallback] = useState(false);

  const fetchOptions = useCallback(async () => {
    try {
      const url =
        kind === "team"
          ? "/api/am/teams"
          : `/api/am/agents?team_id=${encodeURIComponent(teamId || "")}`;
      setOptions(await api<AmItem[]>(url));
      // 上一次失败降级成手输框后，重新展开还能回到下拉（hub 恢复后自愈）
      setFallback(false);
    } catch (e: any) {
      toast.error(t("amSelect.loadFailed", { error: e.message }));
      setFallback(true);
    }
  }, [kind, teamId, t]);

  const onOpen = () => {
    if (kind === "agent" && !teamId) {
      toast.info(t("amSelect.chooseTeamFirst"));
      return;
    }
    fetchOptions();
  };

  // 已存 id 在重开弹窗时要解析回名字
  useEffect(() => {
    if (value && (kind === "team" || teamId)) fetchOptions();
  }, [value, kind, teamId, fetchOptions]);

  const selectedName = fallback
    ? (value ?? undefined)
    : options.find((o) => o.id === value)?.name;
  useEffect(() => {
    onLabelChange?.(selectedName);
  }, [selectedName, onLabelChange]);

  if (fallback) {
    return (
      <div style={style}>
        <Input
          value={value ?? ""}
          onChange={(v) => onChange?.(v)}
          placeholder={placeholder}
          leftIcon={prefix}
          classNames={{ field: "rounded-lg" }}
        />
      </div>
    );
  }
  return (
    <SearchSelect
      value={value ?? null}
      onChange={(v) => onChange?.(v == null ? undefined : String(v))}
      options={options.map((o) => ({
        value: o.id,
        label: o.description
          ? `${o.name}（${o.description.slice(0, 20)}）`
          : o.name,
      }))}
      placeholder={placeholder}
      onOpen={onOpen}
      leftIcon={prefix}
      style={style}
      className={size === "small" ? "h-8 text-xs" : undefined}
    />
  );
}