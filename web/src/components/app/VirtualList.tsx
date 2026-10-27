import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

/** Fixed-height row virtualization for long logs (roadmap.md performance budget: 100k rows). */
export function VirtualList<T>({
  items,
  rowHeight,
  render,
  follow,
  className,
  empty,
}: {
  items: T[];
  rowHeight: number;
  render: (item: T, index: number) => ReactNode;
  follow?: boolean;
  className?: string;
  empty?: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(200);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setHeight(el.clientHeight));
    ro.observe(el);
    setHeight(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (follow && el) el.scrollTop = el.scrollHeight;
  }, [items.length, follow]);

  if (!items.length && empty) return <div className={className}>{empty}</div>;

  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - 10);
  const last = Math.min(items.length, Math.ceil((scrollTop + height) / rowHeight) + 10);
  const rows = [];
  for (let i = first; i < last; i++) {
    rows.push(
      <div key={i} style={{ position: "absolute", top: i * rowHeight, height: rowHeight, left: 0, right: 0 }}>
        {render(items[i], i)}
      </div>,
    );
  }
  return (
    <div ref={ref} className={className} style={{ overflow: "auto", position: "relative" }} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}>
      <div style={{ height: items.length * rowHeight, position: "relative" }}>{rows}</div>
    </div>
  );
}
