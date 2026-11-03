import { useCallback, useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Input, Select, message } from "antd";
import { useTranslation } from "react-i18next";
import { api } from "../api";

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
  size,
  style,
  prefix,
}: {
  kind: "team" | "agent";
  teamId?: string; // kind=agent 时生效
  placeholder?: string;
  value?: string | null;
  onChange?: (v: string | undefined) => void;
  size?: "small" | "middle" | "large";
  style?: CSSProperties;
  prefix?: ReactNode;
}) {
  const { t } = useTranslation();
  const [options, setOptions] = useState<AmItem[]>([]);
  const [fallback, setFallback] = useState(false);

  const load = useCallback(
    async (open: boolean) => {
      if (!open) return;
      if (kind === "agent" && !teamId) {
        message.info(t("amSelect.chooseTeamFirst"));
        return;
      }
      try {
        const url =
          kind === "team" ? "/api/am/teams" : `/api/am/agents?team_id=${encodeURIComponent(teamId || "")}`;
        setOptions(await api<AmItem[]>(url));
        // 上一次失败降级成手输框后，重新展开还能回到下拉（hub 恢复后自愈）
        setFallback(false);
      } catch (e: any) {
        message.error(t("amSelect.loadFailed", { error: e.message }));
        setFallback(true);
      }
    },
    [kind, teamId, t],
  );

  // 已存 id 在重开弹窗时要解析回名字：antd Select 找不到匹配 option 会直接显示原始 id
  useEffect(() => {
    if (value && (kind === "team" || teamId)) load(true);
  }, [value, kind, teamId, load]);

  if (fallback) {
    return (
      <Input
        size={size}
        style={style}
        prefix={prefix}
        placeholder={placeholder}
        value={value ?? ""}
        onChange={(e) => onChange?.(e.target.value)}
      />
    );
  }
  return (
    <Select
      showSearch
      allowClear
      size={size}
      style={style}
      prefix={prefix}
      placeholder={placeholder}
      onOpenChange={load}
      onChange={onChange}
      value={value ?? undefined}
      optionFilterProp="label"
      options={options.map((o) => ({
        value: o.id,
        label: o.description ? `${o.name}（${o.description.slice(0, 20)}）` : o.name,
      }))}
    />
  );
}
