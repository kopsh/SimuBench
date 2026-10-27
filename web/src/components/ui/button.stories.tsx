import type { Meta, StoryObj } from "@storybook/react-vite";
import { Play, Square } from "lucide-react";
import { Badge } from "./badge";
import { Button } from "./button";

const meta: Meta<typeof Button> = { title: "Primitives/Button", component: Button, args: { children: "启动" } };
export default meta;
type Story = StoryObj<typeof Button>;

export const Default: Story = {};
export const Variants: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-2">
      <Button>
        <Play className="size-3.5" /> 启动
      </Button>
      <Button variant="outline">回放并比较</Button>
      <Button variant="secondary">次要</Button>
      <Button variant="ghost" size="icon" aria-label="停止">
        <Square className="size-3.5" />
      </Button>
      <Button variant="destructive">删除</Button>
      <Button disabled>不可用</Button>
      <Badge variant="secondary">3 台仪表</Badge>
    </div>
  ),
};
