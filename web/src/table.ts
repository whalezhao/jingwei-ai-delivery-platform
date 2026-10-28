import type { TablePaginationConfig } from "antd";

/** 全站列表页统一的分页配置（同 aster-qa）：每页 20 条、可切页大小、显示总数。 */
export const PAGE: TablePaginationConfig = {
  pageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: [10, 20, 50, 100],
  showTotal: (total) => `共 ${total} 条`,
};
