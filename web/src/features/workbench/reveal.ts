/**
 * Show one setting of the inspector (#800): scroll to its row, focus its control, mark it for a moment.
 * The row may not exist yet (the channel or the group opens on the same request), so it is looked
 * for on the next frames; `done(false)` says it was not there after `tries` frames and the focus
 * was left alone (#819). Returns the way to stop looking.
 */
export function showSetting(
  path: string,
  done: (found: boolean) => void,
  tries = 20,
  root: ParentNode = document,
): () => void {
  let frame = 0;
  let left = tries;
  const find = () => {
    const row = root.querySelector<HTMLElement>(`[data-setting="${CSS.escape(path)}"]`);
    if (!row) {
      if (--left > 0) frame = requestAnimationFrame(find);
      else done(false);
      return;
    }
    row.scrollIntoView?.({ block: "center" });
    row.querySelector<HTMLElement>("input, button, [role=switch], [role=combobox]")?.focus({ preventScroll: true });
    row.setAttribute("data-revealed", "");
    window.setTimeout(() => row.removeAttribute("data-revealed"), 1600);
    done(true);
  };
  frame = requestAnimationFrame(find);
  return () => cancelAnimationFrame(frame);
}
