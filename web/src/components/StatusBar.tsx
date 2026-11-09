import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";

interface Status {
  runners: { online: number; total: number };
  queue: { running: number; queued: number };
  am: boolean;
}

/** 底部运行状态栏：轮询聚合计数；任何一次轮询失败就把连接点变红。 */
export default function StatusBar() {
  const { t } = useTranslation();
  const [s, setS] = useState<Status | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    let stop = false;
    const load = () =>
      api<Status>("/api/status")
        .then((d) => {
          if (stop) return;
          setS(d);
          setDown(false);
        })
        .catch(() => !stop && setDown(true));
    load();
    const id = setInterval(load, 15000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, []);

  return (
    <footer className="sticky bottom-0 z-30 flex shrink-0 items-center overflow-x-auto whitespace-nowrap border-t border-border bg-background/90 px-4 py-3 text-[11px] text-muted-foreground backdrop-blur-xl">
      {/* ml-auto 而不是 justify-end：内容超宽时可以正常向左滚动，不会被裁掉开头 */}
      <div className="ml-auto flex items-center gap-3">
        <span className="inline-flex shrink-0 items-center gap-1.5">
          <span
            className={`size-1.5 rounded-full ${down ? "bg-danger" : "bg-success"}`}
          />
          {down ? t("statusBar.down") : t("statusBar.ok")}
        </span>
        {s && (
          <>
            <span className="text-border">|</span>
            <span>
              {t("statusBar.runners", {
                online: s.runners.online,
                total: s.runners.total,
              })}
            </span>
            <span className="text-border">|</span>
            <span>
              {t("statusBar.queue", {
                running: s.queue.running,
                queued: s.queue.queued,
              })}
            </span>
            {s.am && (
              <>
                <span className="text-border">|</span>
                <span>{t("statusBar.amOn")}</span>
              </>
            )}
          </>
        )}
      </div>
    </footer>
  );
}
