import type { Meta, StoryObj } from "@storybook/react-vite";
import { FolderOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState } from "./StateViews";

const meta: Meta = { title: "App/State views" };
export default meta;

export const Empty: StoryObj = {
  render: () => (
    <EmptyState
      icon={<FolderOpen className="size-6" />}
      title="工作区中还没有测试台定义"
      body="在工作区中放入 *.bench.yaml 文件，或用 simubench serve 指定一个测试台文件。"
      action={<Button size="sm">新建测试台</Button>}
    />
  ),
};

export const Error: StoryObj = {
  render: () => <ErrorState error={new globalThis.Error("连接被拒绝：本地服务没有运行")} onRetry={() => undefined} />,
};
