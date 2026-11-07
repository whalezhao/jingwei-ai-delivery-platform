import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "../components/motion/center-morph-modal";
import { Button } from "../components/motion/button/index";

export type ConfirmOptions = {
  title: ReactNode;
  description?: ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
};

type State = { opts: ConfirmOptions; resolve: (ok: boolean) => void } | null;

let state: State = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function settle(ok: boolean) {
  const current = state;
  state = null;
  emit();
  current?.resolve(ok);
}

/** Promise 化的确认框，替代 antd Popconfirm / Modal.confirm。 */
export function confirm(opts: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    state = { opts, resolve };
    emit();
  });
}

export function ConfirmHost() {
  const { t } = useTranslation();
  const [current, setCurrent] = useState(state);

  useEffect(() => {
    const l = () => setCurrent(state);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);

  const opts = current?.opts;
  const label = typeof opts?.title === "string" ? opts.title : t("common.confirm");

  return (
    <CenterMorphModal
      open={!!current}
      onOpenChange={(open) => {
        if (!open) settle(false);
      }}
    >
      <CenterMorphModalContent
        ariaLabel={label}
        showCloseButton={false}
        className="max-w-[400px]"
      >
        {opts ? (
          <div className="p-6">
            <h2 className="m-0 text-base font-semibold text-foreground">
              {opts.title}
            </h2>
            {opts.description ? (
              <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
                {opts.description}
              </p>
            ) : null}
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => settle(false)}
              >
                {opts.cancelText ?? t("common.cancel")}
              </Button>
              <Button
                size="sm"
                className={
                  opts.danger
                    ? "rounded-lg bg-destructive hover:bg-destructive/90"
                    : "rounded-lg"
                }
                onClick={() => settle(true)}
              >
                {opts.confirmText ?? t("common.confirm")}
              </Button>
            </div>
          </div>
        ) : null}
      </CenterMorphModalContent>
    </CenterMorphModal>
  );
}