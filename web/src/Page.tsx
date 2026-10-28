import { ReactNode } from "react";
import { PageContainer } from "@ant-design/pro-components";

/**
 * 统一页面骨架（同 aster-qa）：PageContainer ghost —— 透明底，
 * 页头自带标题 / 副标题 / 右侧动作，留白由 Pro 管理，不再手写。
 */
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
    <PageContainer
      ghost
      header={{
        title,
        subTitle: desc,
        extra: actions,
      }}
    >
      {children}
    </PageContainer>
  );
}
