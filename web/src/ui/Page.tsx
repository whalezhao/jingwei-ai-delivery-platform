import type { ReactNode } from "react";

/** 统一页面骨架：标题 / 说明 / 右侧动作 + 内容区。 */
export default function Page({
  title,
  desc,
  actions,
  children,
}: {
  title: ReactNode;
  desc?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="px-4 py-4 sm:px-6 sm:py-5">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="m-0 text-[21px] font-semibold leading-tight tracking-[-0.02em] text-foreground">
            {title}
          </h1>
          {desc ? (
            <p className="mt-1 max-w-[760px] text-[13px] leading-relaxed text-muted-foreground">
              {desc}
            </p>
          ) : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 items-center gap-2">{actions}</div>
        ) : null}
      </header>
      {children}
    </section>
  );
}