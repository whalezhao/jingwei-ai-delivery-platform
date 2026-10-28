import { Table, type TableProps } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Resizable } from "react-resizable";

const MIN_WIDTH = 60;

function ResizableTitle(props: any) {
  const { onResize, width, ...rest } = props;
  if (!width) return <th {...rest} />;
  return (
    <Resizable
      width={width}
      height={0}
      handle={<span className="col-resize-handle" onClick={(e) => e.stopPropagation()} />}
      onResize={onResize}
      draggableOpts={{ enableUserSelectHack: false }}
    >
      <th {...rest} />
    </Resizable>
  );
}

const colKey = (col: any, i: number) => String(col.key ?? col.dataIndex ?? i);

/** antd Table + 表头拖拽调列宽（列宽记在组件内，页面不用管）。 */
export default function ResizableTable<T extends object>({ columns, components, ...rest }: TableProps<T>) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [widths, setWidths] = useState<Record<string, number>>({});
  const widthsRef = useRef(widths);
  widthsRef.current = widths;

  const cols = useMemo(
    () =>
      (columns as ColumnsType<T> | undefined)?.map((col, i) => {
        const key = colKey(col, i);
        const width = widths[key] ?? (typeof col.width === "number" ? col.width : undefined);
        return {
          ...col,
          width,
          onHeaderCell: () => ({
            width,
            "data-col-key": key,
            onResize: (_: unknown, d: { size: { width: number } }) =>
              setWidths((prev) => ({ ...prev, [key]: Math.max(MIN_WIDTH, Math.round(d.size.width)) })),
          }),
        } as any;
      }),
    [columns, widths],
  );

  // 没写 width 的列先量一次当前实际宽度再固定住，否则这些列没有把手可拖（量到 0 说明表还没渲染出来，跳过）。
  useLayoutEffect(() => {
    const ths = wrapRef.current?.querySelectorAll<HTMLTableCellElement>("thead th[data-col-key]");
    if (!ths?.length) return;
    const fixed = new Set(
      (columns as ColumnsType<T> | undefined)?.map((c, i) => (typeof c.width === "number" ? colKey(c, i) : "")),
    );
    const add: Record<string, number> = {};
    ths.forEach((th) => {
      const key = th.dataset.colKey;
      if (key && !fixed.has(key) && widthsRef.current[key] === undefined) {
        const w = Math.round(th.getBoundingClientRect().width);
        if (w > 0) add[key] = w;
      }
    });
    if (Object.keys(add).length) setWidths((prev) => ({ ...prev, ...add }));
  }, [columns]);

  return (
    <div ref={wrapRef} style={{ overflowX: "auto" }}>
      <Table<T>
        {...rest}
        columns={cols as ColumnsType<T>}
        components={{ ...components, header: { ...components?.header, cell: ResizableTitle } }}
      />
    </div>
  );
}
