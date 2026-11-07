import { Info } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Tooltip } from "../components/motion/tooltip";

export type Rule = { required?: boolean; message?: string };
type Errors = Record<string, string | undefined>;

/** 够用就好的表单状态：值、错误、重置、必填校验。不引第三方表单库。 */
export function useForm<T extends Record<string, any>>(initial: T) {
  const [values, setValues] = useState<T>(initial);
  const [errors, setErrors] = useState<Errors>({});

  const set = <K extends keyof T & string>(key: K, value: T[K]) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => ({ ...prev, [key]: undefined }));
  };

  const reset = (next: T = initial) => {
    setValues(next);
    setErrors({});
  };

  const validate = (rules: Record<string, Rule>) => {
    const next: Errors = {};
    for (const [key, rule] of Object.entries(rules)) {
      if (!rule.required) continue;
      const value = values[key as keyof T];
      const empty =
        value == null ||
        value === "" ||
        (Array.isArray(value) && value.length === 0);
      if (empty) next[key] = rule.message ?? "Required";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  return { values, errors, set, reset, validate };
}

/** 给没有内建 label/error 的控件（MultiSelect 等）补上同样的字段外壳。 */
export function Field({
  label,
  hint,
  tip,
  error,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  /** 悬浮说明：替代 antd Form.Item 的 tooltip 图标。 */
  tip?: ReactNode;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="flex items-center gap-1 px-1 text-sm font-medium text-foreground">
        {label}
        {tip ? (
          <Tooltip content={tip}>
            <span className="text-muted-foreground">
              <Info className="size-3.5" />
            </span>
          </Tooltip>
        ) : null}
      </span>
      {children}
      {error ? (
        <span className="px-1 text-xs text-destructive">{error}</span>
      ) : hint ? (
        <span className="px-1 text-xs text-muted-foreground">{hint}</span>
      ) : null}
    </div>
  );
}