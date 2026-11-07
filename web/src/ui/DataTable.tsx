import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { Table, type TableColumn } from "../components/motion/table";
import { Pagination } from "../components/motion/pagination";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/motion/popover";
import { cn } from "@/lib/utils";

export interface DataTableColumn<T> {
  title: ReactNode;
  dataIndex?: keyof T & string;
  key?: string;
  width?: number;
  align?: "left" | "center" | "right";
  render?: (value: any, row: T, index: number) => ReactNode;
  sortable?: boolean;
  sortValue?: (row: T) => string | number;
}

export interface DataTableProps<T> {
  rowKey: keyof T | ((row: T) => string | number);
  dataSource: T[];
  columns: DataTableColumn<T>[];
  loading?: boolean;
  onRowClick?: (row: T) => void;
  emptyText?: ReactNode;
  rowHeight?: number;
  defaultPageSize?: number;
  pageSizeOptions?: number[];
  className?: string;
  /** 服务端分页：传当前页与总数后，表格不再本地切页，也隐藏每页条数。 */
  serverPagination?: {
    page: number;
    total: number;
    pageSize?: number;
    onPageChange: (page: number) => void;
  };
}

/**
 * beUI Table 的应用层包装：列映射、定高虚拟滚动、分页脚、行点击、空态。
 * 表格内部滚动；高度按卡片在视口中的位置算，窗口变化时重算。
 */
export default function DataTable<T extends object>({
  rowKey,
  dataSource,
  columns,
  loading = false,
  onRowClick,
  emptyText,
  rowHeight = 52,
  defaultPageSize = 20,
  pageSizeOptions = [10, 20, 50, 100],
  className,
  serverPagination,
}: DataTableProps<T>) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(defaultPageSize);
  const [sizeOpen, setSizeOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(440);
  // 定高行里被省略号截断的内容，悬停时给出完整文本
  const [tip, setTip] = useState<{
    text: string;
    x: number;
    y: number;
    above: boolean;
  } | null>(null);
  const tipCell = useRef<HTMLElement | null>(null);
  const hideTip = () => {
    tipCell.current = null;
    setTip(null);
  };
  const showTip = (event: ReactPointerEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    // 单元格里的控件自己带标签，别再弹一层
    if (
      target.closest("button,a,input,select,textarea,[role='combobox']")
    )
      return hideTip();
    const td = target.closest("td");
    if (!td) return hideTip();
    if (td === tipCell.current) return;
    const clipped =
      td.scrollWidth > td.clientWidth + 2
        ? td
        : [...td.querySelectorAll<HTMLElement>("span")].find(
            (el) => el.scrollWidth > el.clientWidth + 2,
          );
    const text = (clipped?.textContent || "").trim();
    if (!clipped || !text) return hideTip();
    const rect = td.getBoundingClientRect();
    const above = window.innerHeight - rect.bottom < 72;
    tipCell.current = td;
    setTip({
      text,
      x: Math.max(8, Math.min(rect.left, window.innerWidth - 308)),
      y: above ? rect.top - 6 : rect.bottom + 6,
      above,
    });
  };

  const isServer = Boolean(serverPagination);
  const currentPage = serverPagination?.page ?? page;
  const currentPageSize = serverPagination?.pageSize ?? pageSize;
  const total = serverPagination?.total ?? dataSource.length;
  const totalPages = Math.max(1, Math.ceil(total / currentPageSize));
  useEffect(() => {
    if (!isServer && page > totalPages) setPage(totalPages);
  }, [isServer, page, totalPages]);

  useEffect(() => {
    const measure = () => {
      const top = rootRef.current?.getBoundingClientRect().top ?? 0;
      setHeight(Math.max(260, Math.round(window.innerHeight - top - 92)));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const pageRows = useMemo(
    () =>
      isServer
        ? dataSource
        : dataSource.slice((page - 1) * pageSize, page * pageSize),
    [isServer, dataSource, page, pageSize],
  );

  // 渲染函数需要行号；对象引用在列表里唯一，用它建索引。
  const indexOf = useMemo(
    () => new Map(dataSource.map((row, i) => [row, i])),
    [dataSource],
  );

  const tableColumns: TableColumn<T>[] = useMemo(
    () =>
      columns.map((column, i) => {
        const key = column.key ?? String(column.dataIndex ?? i);
        return {
          key,
          header: column.title,
          align: column.align,
          width: column.width ? `${column.width}px` : undefined,
          sortable: column.sortable,
          sortValue:
            column.sortValue ??
            (column.dataIndex
              ? (row: T) => row[column.dataIndex as keyof T] as string | number
              : undefined),
          cell: column.render
            ? (row: T) =>
                column.render!(
                  column.dataIndex ? row[column.dataIndex] : undefined,
                  row,
                  indexOf.get(row) ?? 0,
                )
            : undefined,
        };
      }),
    [columns, indexOf],
  );

  const getRowId =
    typeof rowKey === "function"
      ? (row: T) => String(rowKey(row))
      : (row: T) => String(row[rowKey]);

  return (
    <div
      ref={rootRef}
      onPointerOver={showTip}
      onPointerLeave={hideTip}
      onScrollCapture={hideTip}
      className={cn(
        "overflow-hidden rounded-xl border border-border bg-card",
        className,
      )}
    >
      <Table<T>
        data={pageRows}
        columns={tableColumns}
        getRowId={(row) => getRowId(row)}
        rowHeight={rowHeight}
        height={height}
        loading={loading}
        onRowClick={onRowClick}
        emptyState={emptyText ?? t("common.noData")}
        className="rounded-none border-0 bg-card"
      />
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2 text-xs text-muted-foreground">
        <span>{t("pagination.total", { total })}</span>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Pagination
            totalPages={totalPages}
            page={currentPage}
            onPageChange={serverPagination?.onPageChange ?? setPage}
            className="[&_button]:h-7 [&_button]:min-w-7 [&_a]:h-7 [&_a]:min-w-7"
          />
          {serverPagination ? null : (
            <Popover
              open={sizeOpen}
              onOpenChange={setSizeOpen}
              align="end"
              sideOffset={8}
            >
              <PopoverTrigger>
                <button
                  type="button"
                  className="h-7 rounded-lg border border-border px-2 tabular-nums text-muted-foreground transition-colors hover:border-(--color-border-strong) hover:text-foreground"
                >
                  {t("pagination.perPage", { n: pageSize })}
                </button>
              </PopoverTrigger>
              <PopoverContent className="w-28 p-1">
                {pageSizeOptions.map((size) => (
                  <button
                    key={size}
                    type="button"
                    onClick={() => {
                      setPageSize(size);
                      setPage(1);
                      setSizeOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors hover:bg-muted",
                      size === pageSize
                        ? "text-foreground"
                        : "text-muted-foreground",
                    )}
                  >
                    {size}
                    {size === pageSize ? (
                      <span className="text-primary">•</span>
                    ) : null}
                  </button>
                ))}
              </PopoverContent>
            </Popover>
          )}
        </div>
      </div>
      {tip ? (
        <div
          role="tooltip"
          className="pointer-events-none fixed z-[70] max-w-[300px] rounded-lg bg-foreground px-2.5 py-1.5 text-[11px] leading-4 break-words text-background shadow-lg"
          style={{
            left: tip.x,
            top: tip.y,
            transform: tip.above ? "translateY(-100%)" : undefined,
          }}
        >
          {tip.text}
        </div>
      ) : null}
    </div>
  );
}