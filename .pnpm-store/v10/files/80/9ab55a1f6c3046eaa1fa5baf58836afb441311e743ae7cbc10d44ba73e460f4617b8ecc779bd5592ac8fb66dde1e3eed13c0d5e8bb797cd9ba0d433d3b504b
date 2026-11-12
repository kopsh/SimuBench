import * as _radix_ui_react_context from '@radix-ui/react-context';
import * as React from 'react';
import * as RovingFocusGroup from '@radix-ui/react-roving-focus';
import { Primitive } from '@radix-ui/react-primitive';
import * as SeparatorPrimitive from '@radix-ui/react-separator';
import * as ToggleGroupPrimitive from '@radix-ui/react-toggle-group';

declare const createToolbarScope: _radix_ui_react_context.CreateScope;
type RovingFocusGroupProps = React.ComponentPropsWithoutRef<typeof RovingFocusGroup.Root>;
type PrimitiveDivProps = React.ComponentPropsWithoutRef<typeof Primitive.div>;
interface ToolbarProps extends PrimitiveDivProps {
    orientation?: RovingFocusGroupProps['orientation'] | undefined;
    loop?: RovingFocusGroupProps['loop'] | undefined;
    dir?: RovingFocusGroupProps['dir'] | undefined;
}
declare const Toolbar: React.ForwardRefExoticComponent<ToolbarProps & React.RefAttributes<HTMLDivElement>>;
type SeparatorProps = React.ComponentPropsWithoutRef<typeof SeparatorPrimitive.Root>;
interface ToolbarSeparatorProps extends SeparatorProps {
}
declare const ToolbarSeparator: React.ForwardRefExoticComponent<ToolbarSeparatorProps & React.RefAttributes<HTMLDivElement>>;
type PrimitiveButtonProps = React.ComponentPropsWithoutRef<typeof Primitive.button>;
interface ToolbarButtonProps extends PrimitiveButtonProps {
}
declare const ToolbarButton: React.ForwardRefExoticComponent<ToolbarButtonProps & React.RefAttributes<HTMLButtonElement>>;
type PrimitiveLinkProps = React.ComponentPropsWithoutRef<typeof Primitive.a>;
interface ToolbarLinkProps extends PrimitiveLinkProps {
}
declare const ToolbarLink: React.ForwardRefExoticComponent<ToolbarLinkProps & React.RefAttributes<HTMLAnchorElement>>;
type ToggleGroupProps = React.ComponentPropsWithoutRef<typeof ToggleGroupPrimitive.Root>;
interface ToolbarToggleGroupSingleProps extends Extract<ToggleGroupProps, {
    type: 'single';
}> {
}
interface ToolbarToggleGroupMultipleProps extends Extract<ToggleGroupProps, {
    type: 'multiple';
}> {
}
declare const ToolbarToggleGroup: React.ForwardRefExoticComponent<(ToolbarToggleGroupSingleProps | ToolbarToggleGroupMultipleProps) & React.RefAttributes<HTMLDivElement>>;
type ToggleGroupItemProps = React.ComponentPropsWithoutRef<typeof ToggleGroupPrimitive.Item>;
interface ToolbarToggleItemProps extends ToggleGroupItemProps {
}
declare const ToolbarToggleItem: React.ForwardRefExoticComponent<ToolbarToggleItemProps & React.RefAttributes<HTMLButtonElement>>;

export { ToolbarButton as Button, ToolbarLink as Link, Toolbar as Root, ToolbarSeparator as Separator, ToolbarToggleGroup as ToggleGroup, ToolbarToggleItem as ToggleItem, Toolbar, ToolbarButton, type ToolbarButtonProps, ToolbarLink, type ToolbarLinkProps, type ToolbarProps, ToolbarSeparator, type ToolbarSeparatorProps, ToolbarToggleGroup, type ToolbarToggleGroupMultipleProps, type ToolbarToggleGroupSingleProps, ToolbarToggleItem, type ToolbarToggleItemProps, createToolbarScope };
