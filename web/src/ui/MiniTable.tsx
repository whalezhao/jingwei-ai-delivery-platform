import type { ReactNode } from "react";
import { Table, type TableColumn } from "../components/motion/table";

/** 统计/明细用的小表：固定行高，高度随行数收敛，不挂分页脚。 */
export default function MiniTable<T extends object>({
  data,
  columns,
  rowKey,
  rowHeight = 36,
  emptyText,
}: {
  data: T[];
  columns: TableColumn<T>[];
  rowKey: keyof T | ((row: T) => string | number);
  rowHeight?: number;
  emptyText?: ReactNode;
}) {
  const getRowId =
    typeof rowKey === "function"
      ? (row: T) => String(rowKey(row))
      : (row: T) => String(row[rowKey]);
  return (
    <Table<T>
      data={data}
      columns={columns}
      getRowId={getRowId}
      rowHeight={rowHeight}
      height={44 + rowHeight * Math.min(Math.max(data.length, 1), 10)}
      emptyState={emptyText}
      className="rounded-lg bg-card"
    />
  );
}