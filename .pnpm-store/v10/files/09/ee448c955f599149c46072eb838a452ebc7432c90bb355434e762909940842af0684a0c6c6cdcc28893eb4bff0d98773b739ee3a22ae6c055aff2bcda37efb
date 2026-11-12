"use client";
import { jsx as X, jsxs as Gn } from "react/jsx-runtime";
import { useId as Nn, useLayoutEffect as cn, useEffect as je, useRef as H, useCallback as se, createContext as Ue, useContext as We, useState as ve, useMemo as pe, useImperativeHandle as St, useSyncExternalStore as at, Children as Fn, isValidElement as ln } from "react";
function it(e) {
  return (typeof e == "number" || typeof e == "string") && e !== "";
}
function Ae(e) {
  const t = Nn();
  return it(e) ? `${e}` : t;
}
const re = typeof window < "u" ? cn : je;
function we(e) {
  const t = H(e);
  return re(() => {
    t.current = e;
  }, [e]), se(
    (...n) => t.current?.(...n),
    [t]
  );
}
function Ye(...e) {
  return we((t) => {
    e.forEach((n) => {
      if (n)
        switch (typeof n) {
          case "function": {
            n(t);
            break;
          }
          case "object": {
            n.current = t;
            break;
          }
        }
    });
  });
}
function Xe(e, t) {
  const n = e?.["data-testid"];
  return it(n) ? `${n}` : it(t) ? `${t}` : void 0;
}
function ht(e, t) {
  return t ? e * 2 + 1 : e + 1;
}
function _n(e, t) {
  return t ? e * 2 - 1 : e;
}
function Tt(e, t, n) {
  const o = ht(e, n);
  return t === void 0 ? `${o} / -1` : `${o} / span ${_n(t, n)}`;
}
function Bn(e) {
  return `${e * 2} / span 1`;
}
function Hn({
  crossCount: e,
  crossSpan: t,
  crossStart: n,
  hasGutters: o
}) {
  const r = t === void 0 ? e : n + t;
  if (n === 0 && r === e)
    return "1 / -1";
  let i = ht(n, o), s = ht(r - 1, o) + 1;
  return o && (n > 0 && i--, r < e && s++), `${i} / ${s}`;
}
function vt(e, t) {
  return Number.isInteger(e) && e >= 1 && e < t;
}
function F(e, t = "Assertion error") {
  if (!e)
    throw Error(t);
}
const un = Ue(null);
function dn() {
  const e = We(un);
  return F(
    e,
    "Grid Context not found; did you render a Cell or Gridline outside of a Grid?"
  ), e;
}
function qn({
  children: e,
  className: t,
  column: n,
  columnSpan: o = 1,
  elementRef: r,
  id: i,
  row: s,
  rowSpan: a = 1,
  style: u,
  ...l
}) {
  const c = Ae(i), { gutters: d, registerCell: p } = dn(), g = H(null), m = Ye(g, r);
  return re(() => {
    const f = g.current;
    if (f !== null)
      return p({
        column: n,
        columnSpan: o,
        element: f,
        id: c,
        row: s,
        rowSpan: a
      });
  }, [n, o, c, p, s, a]), /* @__PURE__ */ X(
    "div",
    {
      ...l,
      className: t,
      "data-cell": !0,
      "data-testid": Xe(l, i),
      id: c,
      ref: m,
      style: {
        overflow: "auto",
        ...u,
        // Allow cells to shrink smaller than their content
        minHeight: 0,
        minWidth: 0,
        gridColumn: Tt(n, o, d.column),
        gridRow: Tt(s, a, d.row)
      },
      children: e
    }
  );
}
qn.displayName = "Cell";
let et;
function Vn() {
  return et === void 0 && (typeof matchMedia == "function" ? et = !!matchMedia("(pointer:coarse)").matches : et = !1), et;
}
function gt({
  expandHitTargets: e,
  axis: t,
  rect: n
}) {
  const o = e ? Vn() ? t.resizeTargetMinimumSize.coarse : t.resizeTargetMinimumSize.fine : 0;
  if (n.width < o) {
    const r = o - n.width;
    n = new DOMRect(
      n.x - r / 2,
      n.y,
      n.width + r,
      n.height
    );
  }
  if (n.height < o) {
    const r = o - n.height;
    n = new DOMRect(
      n.x,
      n.y - r / 2,
      n.width,
      n.height + r
    );
  }
  return n;
}
function At({
  axis: e,
  cells: t,
  index: n,
  rect: o
}) {
  let r = [o];
  for (const i of t) {
    const s = e === "column" ? i.column : i.row, a = e === "column" ? i.columnSpan : i.rowSpan;
    if (s >= n || s + a <= n)
      continue;
    const u = i.element.getBoundingClientRect(), l = e === "column" ? u.top : u.left, c = e === "column" ? u.bottom : u.right;
    c <= l || (r = r.flatMap((d) => {
      const p = e === "column" ? d.top : d.left, g = e === "column" ? d.bottom : d.right;
      if (l >= g || c <= p)
        return [d];
      const m = [], f = (y, S) => {
        m.push(
          e === "column" ? new DOMRect(d.x, y, d.width, S - y) : new DOMRect(y, d.y, S - y, d.height)
        );
      };
      return p < l && f(p, l), g > c && f(c, g), m;
    }));
  }
  return r;
}
function jn({
  axis: e,
  cells: t,
  crossTrackCount: n,
  expandHitTargets: o,
  resizeAxis: r,
  axisSize: i,
  includeDisabled: s,
  separatorPlacements: a
}) {
  const { items: u, separators: l } = r, c = u.length;
  let d = -1, p = -1, g = 0;
  if (u.forEach((b, w) => {
    b.constraintProps.disabled || (g++, d === -1 && (d = w), p = w);
  }), !s && g < 2)
    return [];
  const m = (b) => s || b > d && b <= p, f = [], y = (b, w, h) => ({
    axis: r,
    axisSize: i,
    items: [u[b - 1], u[b]],
    rect: w,
    separator: h
  }), S = (b, w) => {
    let h;
    for (const I of t) {
      const L = e === "column" ? I.row : I.column, M = e === "column" ? I.rowSpan : I.columnSpan;
      if (w === "start" ? L === b : L + M - 1 === b) {
        const T = I.element.getBoundingClientRect();
        if (w === "start") {
          const E = e === "column" ? T.top : T.left;
          h = h === void 0 ? E : Math.min(h, E);
        } else {
          const E = e === "column" ? T.bottom : T.right;
          h = h === void 0 ? E : Math.max(h, E);
        }
      }
    }
    return h;
  }, v = /* @__PURE__ */ new Set();
  for (const b of l) {
    const w = a.get(b);
    if (!w || w.index < 1 || w.index >= c || (v.add(w.index), !s && (b.disabled || b.element.hasAttribute("aria-disabled"))) || !m(w.index))
      continue;
    let h = b.element.getBoundingClientRect();
    const { crossSpan: I, crossStart: L } = w, M = I === void 0 ? n : L + I;
    let T = e === "column" ? h.top : h.left, E = e === "column" ? h.bottom : h.right;
    if (L > 0) {
      const G = S(L - 1, "end");
      G !== void 0 && G < T && (T = G);
    }
    if (M < n) {
      const G = S(M, "start");
      G !== void 0 && G > E && (E = G);
    }
    h = e === "column" ? new DOMRect(h.x, T, h.width, E - T) : new DOMRect(T, h.y, E - T, h.height), h = gt({ expandHitTargets: o, axis: r, rect: h }), s ? f.push(y(w.index, h, b)) : At({
      axis: e,
      cells: t,
      index: w.index,
      rect: h
    }).forEach((G) => {
      f.push(y(w.index, G, b));
    });
  }
  const z = (b, w) => {
    At({
      axis: e,
      cells: t,
      index: b,
      rect: gt({ expandHitTargets: o, axis: r, rect: w })
    }).forEach((h) => {
      f.push(y(b, h));
    });
  }, x = Array.from(
    { length: n },
    () => new Array(c)
  );
  for (const b of t) {
    const [w, h, I, L] = e === "column" ? [b.column, b.columnSpan, b.row, b.rowSpan] : [b.row, b.rowSpan, b.column, b.columnSpan];
    for (let M = Math.max(0, I); M < Math.min(n, I + L); M++)
      for (let T = Math.max(0, w); T < Math.min(c, w + h); T++)
        x[M][T] ??= b;
  }
  const R = /* @__PURE__ */ new Map(), k = (b) => {
    let w = R.get(b);
    if (!w) {
      const h = b.element.getBoundingClientRect();
      w = e === "column" ? {
        crossEnd: h.bottom,
        crossStart: h.top,
        end: h.right,
        start: h.left
      } : {
        crossEnd: h.right,
        crossStart: h.left,
        end: h.bottom,
        start: h.top
      }, R.set(b, w);
    }
    return w;
  }, O = ({ crossEnd: b, crossStart: w, end: h, start: I }) => e === "column" ? new DOMRect(I, w, h - I, b - w) : new DOMRect(w, I, b - w, h - I);
  for (let b = 1; b < c; b++) {
    if (v.has(b) || !m(b))
      continue;
    let w;
    for (let h = 0; h < n; h++) {
      const I = x[h][b - 1], L = x[h][b];
      let M;
      if ((I || L) && I !== L) {
        const T = I ? k(I) : void 0, E = L ? k(L) : void 0, G = T?.end ?? E.start, N = E?.start ?? T.end, P = Math.max(
          T?.crossStart ?? -1 / 0,
          E?.crossStart ?? -1 / 0
        ), A = Math.min(
          T?.crossEnd ?? 1 / 0,
          E?.crossEnd ?? 1 / 0
        );
        M = {
          crossEnd: Math.max(P, A),
          crossStart: P,
          end: Math.max(G, N),
          start: Math.min(G, N)
        };
      }
      M ? w = w ? {
        crossEnd: Math.max(w.crossEnd, M.crossEnd),
        crossStart: Math.min(w.crossStart, M.crossStart),
        end: Math.max(w.end, M.end),
        start: Math.min(w.start, M.start)
      } : M : w && (z(b, O(w)), w = void 0);
    }
    w && z(b, O(w));
  }
  return f;
}
function fe(e) {
  const t = parseFloat(e ?? "");
  return Number.isFinite(t) ? t : 0;
}
function Un({
  axis: e,
  element: t,
  gutterSizes: n,
  hasGutters: o,
  layout: r,
  trackIds: i
}) {
  const s = i.length, a = o ? s * 2 - 1 : s, u = t.ownerDocument.defaultView?.getComputedStyle(t), l = e === "column" ? u?.gridTemplateColumns : u?.gridTemplateRows;
  if (l) {
    const f = l.trim().split(/\s+/);
    if (f.length === a && f.every((y) => /^-?[\d.]+px$/.test(y))) {
      const y = i.map(
        (S, v) => fe(f[o ? v * 2 : v])
      );
      return {
        availableSize: y.reduce((S, v) => S + v, 0),
        trackSizes: y
      };
    }
  }
  const c = e === "column" ? t.offsetWidth : t.offsetHeight, d = e === "column" ? fe(u?.paddingLeft) + fe(u?.paddingRight) + fe(u?.borderLeftWidth) + fe(u?.borderRightWidth) : fe(u?.paddingTop) + fe(u?.paddingBottom) + fe(u?.borderTopWidth) + fe(u?.borderBottomWidth), p = fe(e === "column" ? u?.columnGap : u?.rowGap), g = n.reduce((f, y) => f + y, 0), m = Math.max(
    0,
    c - d - p * Math.max(0, a - 1) - g
  );
  return {
    availableSize: m,
    trackSizes: i.map(
      (f) => r && r[f] !== void 0 ? r[f] / 100 * m : m / s
    )
  };
}
function fn(e) {
  switch (typeof e) {
    case "number":
      return [e, "px"];
    case "string": {
      const t = parseFloat(e);
      return e.endsWith("%") ? [t, "%"] : e.endsWith("px") ? [t, "px"] : e.endsWith("rem") ? [t, "rem"] : e.endsWith("em") ? [t, "em"] : e.endsWith("vh") ? [t, "vh"] : e.endsWith("vw") ? [t, "vw"] : [t, "%"];
    }
  }
}
class mn {
  #e = {};
  addListener(t, n) {
    const o = this.#e[t];
    return o === void 0 ? this.#e[t] = [n] : o.includes(n) || o.push(n), () => {
      this.removeListener(t, n);
    };
  }
  emit(t, n) {
    const o = this.#e[t];
    if (o !== void 0) {
      if (o.length === 1) {
        o[0].call(null, n);
        return;
      }
      let r;
      const i = Array.from(o);
      for (let s = 0; s < i.length; s++) {
        const a = i[s];
        try {
          a.call(null, n);
        } catch (u) {
          r ??= { error: u };
        }
      }
      if (r)
        throw r.error;
    }
  }
  removeAllListeners() {
    this.#e = {};
  }
  removeListener(t, n) {
    const o = this.#e[t];
    if (o !== void 0) {
      const r = o.indexOf(n);
      r >= 0 && o.splice(r, 1);
    }
  }
}
function Wn(e) {
  const t = (o) => {
    o.preventDefault(), o.stopPropagation(), n();
  }, n = () => {
    e.removeEventListener("click", t, !0);
  };
  e.addEventListener("click", t, !0), setTimeout(n, 0);
}
let Y = {
  cursorFlags: 0,
  state: "inactive"
};
const bt = new mn();
function ae() {
  return Y;
}
function xt(e) {
  return bt.addListener("change", e);
}
function Yn(e, t = [], n, o = !1) {
  const r = Y, i = { ...Y };
  i.cursorFlags = e, i.state === "active" && (i.didPointerMove ||= o, i.previews = t, n && (i.previewLayoutMap = n)), Y = i, bt.emit("change", {
    prev: r,
    next: i
  });
}
function he(e) {
  const t = Y;
  Y = e, t.state === "active" && e.state !== "active" && t.didPointerMove && (t.hitRegions.forEach(({ separator: n }) => {
    n && n.element.ownerDocument.activeElement === n.element && n.element.blur();
  }), Wn(t.ownerDocument)), bt.emit("change", {
    prev: t,
    next: e
  });
}
function Xn(e) {
  Y.state !== "active" || !Y.previews.some((t) => t.separator === e) || he({
    ...Y,
    previews: Y.previews.map(
      (t) => t.separator === e ? { ...t } : t
    )
  });
}
function Kn(e) {
  if (Y.state === "inactive")
    return;
  const t = Y.hitRegions.filter((o) => o.axis !== e), n = Y.state === "active" && Y.previews.some((o) => o.axis === e);
  if (!(t.length === Y.hitRegions.length && !n))
    if (t.length === 0)
      he({ cursorFlags: 0, state: "inactive" });
    else if (Y.state === "active") {
      const o = new Map(Y.initialLayoutMap), r = new Map(Y.previewLayoutMap);
      o.delete(e), r.delete(e), he({
        ...Y,
        cursorFlags: 0,
        hitRegions: t,
        initialLayoutMap: o,
        previewLayoutMap: r,
        previews: Y.previews.filter((i) => i.axis !== e)
      });
    } else
      he({ ...Y, hitRegions: t });
}
const Jn = (e) => e, pt = () => {
}, pn = 1, hn = 2, gn = 4, yn = 8, $t = 3, Dt = 12;
let tt;
function Ot() {
  return tt === void 0 && (tt = !1, typeof window < "u" && (window.navigator.userAgent.includes("Chrome") || window.navigator.userAgent.includes("Firefox")) && (tt = !0)), tt;
}
function Zn({
  cursorFlags: e,
  axes: t,
  state: n
}) {
  let o = 0, r = 0;
  switch (n) {
    case "active":
    case "hover":
      t.forEach((i) => {
        if (!i.mutableState.disableCursor)
          switch (i.orientation) {
            case "horizontal": {
              o++;
              break;
            }
            case "vertical": {
              r++;
              break;
            }
          }
      });
  }
  if (!(o === 0 && r === 0)) {
    switch (n) {
      case "active": {
        if (e && Ot()) {
          const i = (e & pn) !== 0, s = (e & hn) !== 0, a = (e & gn) !== 0, u = (e & yn) !== 0;
          if (i)
            return a ? "se-resize" : u ? "ne-resize" : "e-resize";
          if (s)
            return a ? "sw-resize" : u ? "nw-resize" : "w-resize";
          if (a)
            return "s-resize";
          if (u)
            return "n-resize";
        }
        break;
      }
    }
    return Ot() ? o > 0 && r > 0 ? "move" : o > 0 ? "ew-resize" : "ns-resize" : o > 0 && r > 0 ? "grab" : o > 0 ? "col-resize" : "row-resize";
  }
}
const Gt = /* @__PURE__ */ new WeakMap();
function Nt(e) {
  if (!e.defaultView || !e.adoptedStyleSheets)
    return;
  let { prevStyle: t, styleSheet: n } = Gt.get(e) ?? {};
  n === void 0 && (n = new e.defaultView.CSSStyleSheet(), e.adoptedStyleSheets && (Object.isExtensible(e.adoptedStyleSheets) ? e.adoptedStyleSheets.push(n) : e.adoptedStyleSheets = [
    ...e.adoptedStyleSheets,
    n
  ]));
  let o = ae();
  switch (o.state !== "inactive" && o.ownerDocument !== e && (o = { cursorFlags: 0, state: "inactive" }), o.state) {
    case "active":
    case "hover": {
      const r = Zn({
        cursorFlags: o.cursorFlags,
        axes: o.hitRegions.map((s) => s.axis),
        state: o.state
      }), i = `*, *:hover {cursor: ${r} !important; }`;
      if (t === i)
        return;
      t = i, r ? n.cssRules.length === 0 ? n.insertRule(i) : n.replaceSync(i) : n.cssRules.length === 1 && n.deleteRule(0);
      break;
    }
    case "inactive": {
      t = void 0, n.cssRules.length === 1 && n.deleteRule(0);
      break;
    }
  }
  Gt.set(e, {
    prevStyle: t,
    styleSheet: n
  });
}
function Ft(e) {
  return e.state === "inactive" ? void 0 : e.ownerDocument;
}
function Qn({
  next: e,
  prev: t
}) {
  const n = Ft(t), o = Ft(e);
  n && n !== o && Nt(n), o && Nt(o);
}
function Ke({
  axis: e
}) {
  const { layoutStrategy: t, orientation: n, items: o } = e;
  return t ? t.calculateAvailableSize() : o.reduce((r, i) => (r += n === "horizontal" ? i.element.offsetWidth : i.element.offsetHeight, r), 0);
}
function wt(e, t) {
  const n = e.compareDocumentPosition(t);
  return n & Node.DOCUMENT_POSITION_DISCONNECTED ? 0 : n & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : n & Node.DOCUMENT_POSITION_PRECEDING ? 1 : 0;
}
function _e(e, t) {
  return Array.from(t).sort((n, o) => {
    const r = e === "horizontal" ? eo(n, o) : to(n, o);
    return r !== 0 ? r : wt(n.element, o.element);
  });
}
function eo(e, t) {
  const n = e.element.offsetLeft - t.element.offsetLeft;
  return n !== 0 ? n : e.element.offsetWidth - t.element.offsetWidth;
}
function to(e, t) {
  const n = e.element.offsetTop - t.element.offsetTop;
  return n !== 0 ? n : e.element.offsetHeight - t.element.offsetHeight;
}
function yt(e) {
  return e !== null && typeof e == "object" && "nodeType" in e && e.nodeType === Node.ELEMENT_NODE;
}
function Sn(e, t) {
  return {
    x: e.x >= t.left && e.x <= t.right ? 0 : Math.min(
      Math.abs(e.x - t.left),
      Math.abs(e.x - t.right)
    ),
    y: e.y >= t.top && e.y <= t.bottom ? 0 : Math.min(
      Math.abs(e.y - t.top),
      Math.abs(e.y - t.bottom)
    )
  };
}
function no({
  orientation: e,
  rects: t,
  targetRect: n
}) {
  const o = {
    x: n.x + n.width / 2,
    y: n.y + n.height / 2
  };
  let r, i = Number.MAX_VALUE;
  for (const s of t) {
    const { x: a, y: u } = Sn(o, s), l = e === "horizontal" ? a : u;
    l < i && (i = l, r = s);
  }
  return F(r, "No rect found"), r;
}
function zt({
  expandHitTargets: e = !0,
  axis: t,
  includeDisabled: n = !1
}) {
  if (t.layoutStrategy)
    return t.layoutStrategy.calculateHitRegions({
      expandHitTargets: e,
      axis: t,
      includeDisabled: n
    });
  const { element: o, orientation: r, items: i, separators: s } = t, a = _e(
    r,
    Array.from(o.children).filter(yt).filter((S) => !S.hasAttribute("data-resize-preview")).map((S) => ({ element: S }))
  ).map(({ element: S }) => S), u = [];
  let l = !1, c = !1, d = -1, p, g = -1, m = 0, f, y = [];
  {
    let S = -1;
    for (const v of a)
      v.hasAttribute("data-panel") && (S++, v.hasAttribute("data-disabled") || (m++, d === -1 && (d = S), g = S));
  }
  if (n || m > 1) {
    let S = -1;
    for (const v of a)
      if (v.hasAttribute("data-panel")) {
        S++;
        const z = i.find(
          (x) => x.element === v
        );
        if (z) {
          if (f) {
            const x = f.element.getBoundingClientRect(), R = v.getBoundingClientRect();
            let k;
            if (c) {
              const O = r === "horizontal" ? new DOMRect(
                x.right,
                x.top,
                0,
                x.height
              ) : new DOMRect(
                x.left,
                x.bottom,
                x.width,
                0
              ), b = r === "horizontal" ? new DOMRect(R.left, R.top, 0, R.height) : new DOMRect(R.left, R.top, R.width, 0);
              switch (y.length) {
                case 0: {
                  k = [
                    O,
                    b
                  ];
                  break;
                }
                case 1: {
                  const w = y[0], h = no({
                    orientation: r,
                    rects: [x, R],
                    targetRect: w.element.getBoundingClientRect()
                  });
                  k = [
                    w,
                    h === x ? b : O
                  ];
                  break;
                }
                default: {
                  k = y;
                  break;
                }
              }
            } else
              y.length ? k = y : k = [
                r === "horizontal" ? new DOMRect(
                  x.right,
                  R.top,
                  R.left - x.right,
                  R.height
                ) : new DOMRect(
                  R.left,
                  x.bottom,
                  R.width,
                  R.top - x.bottom
                )
              ];
            for (const O of k) {
              const b = gt({
                expandHitTargets: e,
                axis: t,
                rect: "width" in O ? O : O.element.getBoundingClientRect()
              }), w = S <= d || S > g;
              (n || !l && !w) && (p ??= Ke({ axis: t }), u.push({
                axis: t,
                axisSize: p,
                items: [f, z],
                separator: "width" in O ? void 0 : O,
                rect: b
              })), l = !1;
            }
          }
          c = !1, f = z, y = [];
        }
      } else if (v.hasAttribute("data-separator")) {
        v.ariaDisabled !== null && (l = !0);
        const z = s.find(
          (x) => x.element === v
        );
        z ? y.push(z) : (f = void 0, y = []);
      } else
        c = !0;
  }
  return u;
}
function oo(e, t) {
  const n = getComputedStyle(e), o = parseFloat(n.fontSize);
  return t * o;
}
function ro(e, t) {
  const n = getComputedStyle(e.ownerDocument.documentElement), o = parseFloat(n.fontSize);
  return t * o;
}
function io(e) {
  return e / 100 * window.innerHeight;
}
function so(e) {
  return e / 100 * window.innerWidth;
}
function Me({
  axisSize: e,
  itemElement: t,
  styleProp: n
}) {
  let o;
  const [r, i] = fn(n);
  switch (i) {
    case "%": {
      o = r / 100 * e;
      break;
    }
    case "px": {
      o = r;
      break;
    }
    case "rem": {
      o = ro(t, r);
      break;
    }
    case "em": {
      o = oo(t, r);
      break;
    }
    case "vh": {
      o = io(r);
      break;
    }
    case "vw": {
      o = so(r);
      break;
    }
  }
  return o;
}
function Q(e) {
  return parseFloat(e.toFixed(3));
}
function Lt(e, t = Ke({ axis: e })) {
  const { items: n } = e;
  return t === 0 ? n.map((o) => ({
    groupResizeBehavior: o.constraintProps.groupResizeBehavior,
    collapsedSize: 0,
    collapsible: o.constraintProps.collapsible === !0,
    defaultSize: void 0,
    disabled: o.constraintProps.disabled,
    minSize: 0,
    maxSize: 100,
    itemId: o.id
  })) : n.map((o) => {
    const { element: r, constraintProps: i } = o;
    let s = 0;
    if (i.collapsedSize !== void 0) {
      const d = Me({
        axisSize: t,
        itemElement: r,
        styleProp: i.collapsedSize
      });
      s = Q(d / t * 100);
    }
    let a;
    if (i.collapsedThreshold !== void 0) {
      const d = Me({
        axisSize: t,
        itemElement: r,
        styleProp: i.collapsedThreshold
      });
      a = Q(d / t * 100);
    }
    let u;
    if (i.defaultSize !== void 0) {
      const d = Me({
        axisSize: t,
        itemElement: r,
        styleProp: i.defaultSize
      });
      u = Q(d / t * 100);
    }
    let l = 0;
    if (i.minSize !== void 0) {
      const d = Me({
        axisSize: t,
        itemElement: r,
        styleProp: i.minSize
      });
      l = Q(d / t * 100);
    }
    let c = 100;
    if (i.maxSize !== void 0) {
      const d = Me({
        axisSize: t,
        itemElement: r,
        styleProp: i.maxSize
      });
      c = Q(d / t * 100);
    }
    return {
      groupResizeBehavior: i.groupResizeBehavior,
      collapsedSize: s,
      collapsedThreshold: a,
      collapsible: i.collapsible === !0,
      defaultSize: u,
      disabled: i.disabled,
      minSize: l,
      maxSize: c,
      itemId: o.id
    };
  });
}
let me = /* @__PURE__ */ new Map();
const It = new mn();
let Be = 0;
const nt = [];
let ot;
function ct() {
  Be++;
}
function Ct() {
  for (let e = 0; e < nt.length; e++)
    try {
      It.emit("axisChange", nt[e]);
    } catch (t) {
      ot ??= { error: t };
    }
  nt.length = 0;
}
function lt() {
  if (Be === 1 && Ct(), Be--, Be === 0 && ot) {
    const { error: e } = ot;
    throw ot = void 0, e;
  }
}
function ao(e) {
  me = new Map(me), me.delete(e);
}
function Te(e, t) {
  for (const [n] of me)
    if (n.id === e)
      return n;
}
function ue(e, t) {
  for (const [n, o] of me)
    if (n.id === e)
      return o;
  if (t)
    throw Error(`Could not find data for Group with id ${e}`);
}
function be() {
  return me;
}
function qe(e, t) {
  return It.addListener("axisChange", (n) => {
    n.axis.id === e && t(n);
  });
}
function ge(e, t, n) {
  const o = me.get(e);
  me = new Map(me), me.set(e, t);
  const r = {
    axis: e,
    isUserInteraction: n?.isUserInteraction === !0,
    prev: o,
    next: t
  };
  Be > 0 ? nt.push(r) : It.emit("axisChange", r);
}
function vn(e, { pointerId: t, pointerType: n }) {
  return t === e.pointerId || // There is only one pen, but it may be assigned a new pointerId each time it comes into range
  n === "pen" && e.pointerType === "pen";
}
function ut(e, t, n = {}) {
  return e.ownerDocument === t && (n.pointerId === void 0 || vn(e, n));
}
function co(e, t) {
  if (e.length !== t.length)
    return !1;
  for (let n = 0; n < e.length; n++)
    if (e[n] != t[n])
      return !1;
  return !0;
}
function W(e, t, n = 0) {
  return Math.abs(Q(e) - Q(t)) <= n;
}
function oe(e, t) {
  return W(e, t) ? 0 : e > t ? 1 : -1;
}
function Ee({
  overrideDisabledItems: e,
  itemConstraints: t,
  prevSize: n,
  size: o
}) {
  const {
    collapsedSize: r = 0,
    collapsedThreshold: i,
    collapsible: s,
    disabled: a,
    maxSize: u = 100,
    minSize: l = 0
  } = t;
  if (a && !e)
    return n;
  if (oe(o, l) < 0)
    if (s) {
      const c = i ?? (l - r) / 2, d = oe(n, r) <= 0, p = d ? r + c : l - c, g = oe(o, p);
      oe(o, r) <= 0 || g < 0 || i !== void 0 && d && g === 0 ? o = r : o = l;
    } else
      o = l;
  return o = Math.min(u, o), o = Q(o), o;
}
function Ve({
  delta: e,
  initialLayout: t,
  itemConstraints: n,
  pivotIndices: o,
  prevLayout: r,
  trigger: i
}) {
  if (W(e, 0))
    return t;
  const s = i === "imperative-api", a = n.map(
    ({ itemId: m }) => t[m]
  ), u = n.map(
    ({ itemId: m }) => r[m]
  ), l = [...a], [c, d] = o;
  F(c != null, "Invalid first pivot index"), F(d != null, "Invalid second pivot index");
  let p = 0;
  switch (i) {
    case "keyboard": {
      {
        const m = e < 0 ? d : c, f = n[m];
        F(
          f,
          `Panel constraints not found for index ${m}`
        );
        const {
          collapsedSize: y = 0,
          collapsible: S,
          minSize: v = 0
        } = f;
        if (S) {
          const z = a[m];
          if (F(
            z != null,
            `Previous layout not found for panel index ${m}`
          ), W(z, y)) {
            const x = v - z;
            oe(x, Math.abs(e)) > 0 && (e = e < 0 ? 0 - x : x);
          }
        }
      }
      {
        const m = e < 0 ? c : d, f = n[m];
        F(
          f,
          `No panel constraints found for index ${m}`
        );
        const {
          collapsedSize: y = 0,
          collapsible: S,
          minSize: v = 0
        } = f;
        if (S) {
          const z = a[m];
          if (F(
            z != null,
            `Previous layout not found for panel index ${m}`
          ), W(z, v)) {
            const x = z - y;
            oe(x, Math.abs(e)) > 0 && (e = e < 0 ? 0 - x : x);
          }
        }
      }
      break;
    }
    default: {
      const m = e < 0 ? d : c, f = n[m];
      F(
        f,
        `Panel constraints not found for index ${m}`
      );
      const y = a[m], { collapsedSize: S, collapsedThreshold: v, collapsible: z, minSize: x } = f;
      if (z && oe(y, x) < 0) {
        const R = x - S, k = v ?? R / 2, O = y + Math.abs(e);
        if (oe(O, x) < 0) {
          const b = oe(Math.abs(e), k), w = v === void 0 && e < 0;
          b > 0 || b === 0 && w ? e = e < 0 ? -R : R : e = 0;
        }
      }
      break;
    }
  }
  {
    const m = e < 0 ? 1 : -1;
    let f = e < 0 ? d : c, y = 0;
    for (; ; ) {
      const v = a[f];
      F(
        v != null,
        `Previous layout not found for panel index ${f}`
      );
      const x = Ee({
        overrideDisabledItems: s,
        itemConstraints: n[f],
        prevSize: v,
        size: 100
      }) - v;
      if (y += x, f += m, f < 0 || f >= n.length)
        break;
    }
    const S = Math.min(Math.abs(e), Math.abs(y));
    e = e < 0 ? 0 - S : S;
  }
  {
    let f = e < 0 ? c : d;
    for (; f >= 0 && f < n.length; ) {
      const y = Math.abs(e) - Math.abs(p), S = a[f];
      F(
        S != null,
        `Previous layout not found for panel index ${f}`
      );
      const v = S - y, z = Ee({
        overrideDisabledItems: s,
        itemConstraints: n[f],
        prevSize: S,
        size: v
      });
      if (!W(S, z) && (p += S - z, l[f] = z, p.toFixed(3).localeCompare(Math.abs(e).toFixed(3), void 0, {
        numeric: !0
      }) >= 0))
        break;
      e < 0 ? f-- : f++;
    }
  }
  if (co(u, l))
    return r;
  {
    const m = e < 0 ? d : c, f = a[m];
    F(
      f != null,
      `Previous layout not found for panel index ${m}`
    );
    const y = f + p, S = Ee({
      overrideDisabledItems: s,
      itemConstraints: n[m],
      prevSize: f,
      size: y
    });
    if (l[m] = S, !W(S, y)) {
      let v = y - S, x = e < 0 ? d : c;
      for (; x >= 0 && x < n.length; ) {
        const R = l[x];
        F(
          R != null,
          `Previous layout not found for panel index ${x}`
        );
        const k = R + v, O = Ee({
          overrideDisabledItems: s,
          itemConstraints: n[x],
          prevSize: R,
          size: k
        });
        if (W(R, O) || (v -= O - R, l[x] = O), W(v, 0))
          break;
        e > 0 ? x-- : x++;
      }
    }
  }
  const g = Object.values(l).reduce(
    (m, f) => f + m,
    0
  );
  return W(g, 100, 0.1) ? l.reduce((m, f, y) => (m[n[y].itemId] = f, m), {}) : r;
}
function le(e, t) {
  if (Object.keys(e).length !== Object.keys(t).length)
    return !1;
  for (const n in e)
    if (t[n] === void 0 || oe(e[n], t[n]) !== 0)
      return !1;
  return !0;
}
function Rt(e) {
  ct();
  try {
    lo(e);
  } finally {
    lt();
  }
}
function lo({
  commit: e,
  event: t,
  hitRegions: n,
  initialLayoutMap: o,
  mountedAxes: r,
  pointerDownAtPoint: i,
  prevCursorFlags: s
}) {
  let a = 0;
  const u = ae();
  let l = u.state === "active" ? u.previews : [];
  const c = new Map(
    u.state === "active" ? u.previewLayoutMap : void 0
  );
  n.forEach((g) => {
    const { axis: m, axisSize: f } = g, { orientation: y, items: S } = m;
    if (e && m.resizePreviewMode !== "separator")
      return;
    const { disableCursor: v } = m.mutableState;
    let z = 0;
    i ? y === "horizontal" ? z = (t.clientX - i.x) / f * 100 : z = (t.clientY - i.y) / f * 100 : y === "horizontal" ? z = t.clientX < 0 ? -100 : 100 : z = t.clientY < 0 ? -100 : 100;
    const x = o.get(m), R = r.get(m);
    if (!x || !R)
      return;
    const {
      defaultLayoutDeferred: k,
      derivedItemConstraints: O,
      axisSize: b,
      layout: w,
      separatorToItems: h
    } = R;
    if (O && w && h) {
      const I = m.resizePreviewMode === "separator" ? c.get(m) ?? w : w, L = Ve({
        delta: z,
        initialLayout: x,
        itemConstraints: O,
        pivotIndices: g.items.map((M) => S.indexOf(M)),
        prevLayout: I,
        trigger: "mouse-or-touch"
      });
      if (m.resizePreviewMode === "separator" && !e && !le(L, I)) {
        c.set(m, L);
        let M = 0;
        const T = S.map((E) => (M += L[E.id] - x[E.id], M * (f / 100)));
        l = l.map((E) => {
          if (E.axis !== m)
            return E;
          const G = T[E.itemIndex];
          return G === E.offset ? E : { ...E, offset: G };
        });
      }
      if (le(L, I) && z !== 0 && !v)
        switch (y) {
          case "horizontal": {
            a |= z < 0 ? pn : hn;
            break;
          }
          case "vertical": {
            a |= z < 0 ? gn : yn;
            break;
          }
        }
      (m.resizePreviewMode !== "separator" || e) && !le(L, w) && ge(g.axis, {
        defaultLayoutDeferred: k,
        derivedItemConstraints: O,
        axisSize: b,
        layout: L,
        requestedAxisSize: b,
        requestedLayout: L,
        separatorToItems: h
      });
    }
  });
  let d = 0;
  t.movementX === 0 ? d |= s & $t : d |= a & $t, t.movementY === 0 ? d |= s & Dt : d |= a & Dt;
  const p = u.state === "active" && (t.clientX !== u.pointerDownAtPoint.x || t.clientY !== u.pointerDownAtPoint.y);
  Yn(d, l, c, p);
}
function bn(e, t) {
  const n = ae();
  if (n.state !== "active" || !ut(n, e, t))
    return;
  const o = be();
  ct();
  try {
    Rt({
      commit: !0,
      event: t,
      hitRegions: n.hitRegions,
      initialLayoutMap: n.initialLayoutMap,
      mountedAxes: o,
      pointerDownAtPoint: n.pointerDownAtPoint,
      prevCursorFlags: n.cursorFlags
    }), Ct(), he({
      cursorFlags: 0,
      state: "inactive"
    }), n.hitRegions.length > 0 && (t.preventDefault?.(), n.hitRegions.forEach((r) => {
      if (!o.has(r.axis))
        return;
      const i = ue(r.axis.id, !0);
      ge(r.axis, i, {
        isUserInteraction: !0
      });
    }));
  } finally {
    lt();
  }
}
function _t(e) {
  if (e.defaultPrevented)
    return;
  const { clientX: t, clientY: n, movementX: o, movementY: r } = e;
  bn(e.currentTarget, {
    clientX: t,
    clientY: n,
    movementX: o,
    movementY: r
  });
}
function uo(e, t, n) {
  let o, r = {
    x: 1 / 0,
    y: 1 / 0
  };
  for (const i of t) {
    const s = Sn(n, i.rect);
    switch (e) {
      case "horizontal": {
        (s.x < r.x || s.x === r.x && s.y <= r.y) && (o = i, r = s);
        break;
      }
      case "vertical": {
        (s.y < r.y || s.y === r.y && s.x <= r.x) && (o = i, r = s);
        break;
      }
    }
  }
  return o ? {
    distance: r,
    hitRegion: o
  } : void 0;
}
function fo(e) {
  return e !== null && typeof e == "object" && "nodeType" in e && e.nodeType === Node.DOCUMENT_FRAGMENT_NODE;
}
function mo(e, t) {
  if (e === t) throw new Error("Cannot compare node with itself");
  const n = {
    a: qt(e),
    b: qt(t)
  };
  let o;
  for (; n.a.at(-1) === n.b.at(-1); )
    o = n.a.pop(), n.b.pop();
  F(
    o,
    "Stacking order can only be calculated for elements with a common ancestor"
  );
  const r = {
    a: Ht(Bt(n.a)),
    b: Ht(Bt(n.b))
  };
  if (r.a === r.b) {
    const i = o.childNodes, s = {
      a: n.a.at(-1),
      b: n.b.at(-1)
    };
    let a = i.length;
    for (; a--; ) {
      const u = i[a];
      if (u === s.a) return 1;
      if (u === s.b) return -1;
    }
  }
  return Math.sign(r.a - r.b);
}
const po = /\b(?:position|zIndex|opacity|transform|webkitTransform|mixBlendMode|filter|webkitFilter|isolation)\b/;
function ho(e) {
  const t = getComputedStyle(xn(e) ?? e).display;
  return t === "flex" || t === "inline-flex";
}
function go(e) {
  const t = getComputedStyle(e);
  return !!(t.position === "fixed" || t.zIndex !== "auto" && (t.position !== "static" || ho(e)) || +t.opacity < 1 || "transform" in t && t.transform !== "none" || "webkitTransform" in t && t.webkitTransform !== "none" || "mixBlendMode" in t && t.mixBlendMode !== "normal" || "filter" in t && t.filter !== "none" || "webkitFilter" in t && t.webkitFilter !== "none" || "isolation" in t && t.isolation === "isolate" || po.test(t.willChange) || t.webkitOverflowScrolling === "touch");
}
function Bt(e) {
  let t = e.length;
  for (; t--; ) {
    const n = e[t];
    if (F(n, "Missing node"), go(n)) return n;
  }
  return null;
}
function Ht(e) {
  return e && Number(getComputedStyle(e).zIndex) || 0;
}
function qt(e) {
  const t = [];
  for (; e; )
    t.push(e), e = xn(e);
  return t;
}
function xn(e) {
  const { parentNode: t } = e;
  return fo(t) ? t.host : t;
}
function yo(e, t) {
  return e.x < t.x + t.width && e.x + e.width > t.x && e.y < t.y + t.height && e.y + e.height > t.y;
}
function So(e) {
  try {
    return e.matches(":modal");
  } catch {
    return !1;
  }
}
function vo({
  axisElement: e,
  hitRegion: t,
  pointerEventTarget: n
}) {
  if (yt(n)) {
    const o = n.closest("dialog");
    if (o && !o.contains(e) && So(o))
      return !1;
  }
  if (!yt(n) || n.contains(e) || e.contains(n) || !n.isConnected || !e.isConnected)
    return !0;
  if (mo(n, e) > 0) {
    let o = n;
    for (; o; ) {
      if (o.contains(e))
        return !0;
      if (yo(o.getBoundingClientRect(), t))
        return !1;
      o = o.parentElement;
    }
  }
  return !0;
}
function Et(e, t, n) {
  const o = [];
  return t.forEach((r, i) => {
    if (i.disabled || i.element.ownerDocument !== n)
      return;
    const s = zt({ axis: i }), a = uo(i.orientation, s, {
      x: e.clientX,
      y: e.clientY
    });
    a && a.distance.x <= 0 && a.distance.y <= 0 && vo({
      axisElement: i.element,
      hitRegion: a.hitRegion.rect,
      pointerEventTarget: e.target
    }) && o.push(a.hitRegion);
  }), o;
}
function bo(e) {
  let t = 0, n = 0;
  const o = {};
  for (const i of e)
    if (i.defaultSize !== void 0) {
      t++;
      const s = Q(i.defaultSize);
      n += s, o[i.itemId] = s;
    } else
      o[i.itemId] = void 0;
  const r = e.length - t;
  if (r !== 0) {
    const i = Q((100 - n) / r);
    for (const s of e)
      s.defaultSize === void 0 && (o[s.itemId] = i);
  }
  return o;
}
function xo(e, t) {
  const n = e.map((r) => r.id), o = Object.keys(t);
  if (n.length !== o.length)
    return !1;
  for (const r of n)
    if (!o.includes(r))
      return !1;
  return !0;
}
function wn({
  axis: e,
  itemConstraints: t
}) {
  const n = e.items.map(({ id: r }) => r).join(","), o = e.mutableState.defaultLayout;
  return e.mutableState.layouts[n] ?? (o && xo(e.items, o) ? o : bo(t));
}
function zn(e, t) {
  if (Object.keys(e).length !== Object.keys(t).length)
    return !1;
  for (const o in e)
    if (e[o] !== t[o])
      return !1;
  return !0;
}
function wo(e, t) {
  return e.length !== t.length ? !1 : e.every((n, o) => zn(n, t[o]));
}
function ze({
  layout: e,
  itemConstraints: t
}) {
  const n = t.map(({ itemId: s }) => e[s]), o = [...n], r = o.reduce(
    (s, a) => s + a,
    0
  );
  if (Object.keys(e).length !== t.length)
    throw Error(
      `Invalid ${t.length} panel layout: ${Object.values(e).map((s) => `${s}%`).join(", ")}`
    );
  if (!W(r, 100) && o.length > 0)
    for (let s = 0; s < t.length; s++) {
      const a = o[s];
      F(a != null, `No layout data found for index ${s}`);
      const u = 100 / r * a;
      o[s] = u;
    }
  let i = 0;
  for (let s = 0; s < t.length; s++) {
    const a = n[s];
    F(a != null, `No layout data found for index ${s}`);
    const u = o[s];
    F(u != null, `No layout data found for index ${s}`);
    const l = Ee({
      overrideDisabledItems: !0,
      itemConstraints: t[s],
      prevSize: a,
      size: u
    });
    u != l && (i += u - l, o[s] = l);
  }
  if (!W(i, 0))
    for (let s = 0; s < t.length; s++) {
      const a = o[s];
      F(a != null, `No layout data found for index ${s}`);
      const u = a + i, l = Ee({
        overrideDisabledItems: !0,
        itemConstraints: t[s],
        prevSize: a,
        size: u
      });
      if (a !== l && (i -= l - a, o[s] = l, W(i, 0)))
        break;
    }
  return o.reduce((s, a, u) => (s[t[u].itemId] = a, s), {});
}
function Ln({
  itemIds: e,
  layout: t
}) {
  return ze({
    layout: t,
    itemConstraints: e.map((n) => ({
      collapsedSize: 0,
      collapsible: !1,
      defaultSize: void 0,
      disabled: void 0,
      itemId: n,
      maxSize: 100,
      minSize: 0
    }))
  });
}
function zo({
  axis: e,
  nextAxisSize: t,
  prevAxisSize: n,
  prevLayout: o
}) {
  if (n <= 0 || t <= 0 || n === t)
    return o;
  let r = 0, i = 0, s = !1;
  const a = /* @__PURE__ */ new Map(), u = [];
  for (const d of e.items) {
    const p = o[d.id] ?? 0;
    switch (d.constraintProps.groupResizeBehavior) {
      case "preserve-pixel-size": {
        s = !0;
        const g = p / 100 * n, m = Q(
          g / t * 100
        );
        a.set(d.id, m), r += m;
        break;
      }
      case "preserve-relative-size":
      default: {
        u.push(d.id), i += p;
        break;
      }
    }
  }
  if (!s || u.length === 0)
    return o;
  const l = 100 - r, c = { ...o };
  if (a.forEach((d, p) => {
    c[p] = d;
  }), i > 0)
    for (const d of u) {
      const p = o[d] ?? 0;
      c[d] = Q(
        p / i * l
      );
    }
  else {
    const d = Q(l / u.length);
    for (const p of u)
      c[p] = d;
  }
  return c;
}
function Pt({
  axis: e,
  axisState: t
}) {
  const n = Ke({ axis: e });
  if (n === 0)
    return;
  const o = Lt(e, n);
  let r = t.requestedAxisSize, i = t.requestedLayout;
  t.defaultLayoutDeferred && (r = n, i = Ln({
    itemIds: e.items.map(({ id: l }) => l),
    layout: wn({
      axis: e,
      itemConstraints: o
    })
  }));
  const s = zo({
    axis: e,
    nextAxisSize: n,
    prevAxisSize: r,
    prevLayout: i
  }), a = ze({
    layout: s,
    itemConstraints: o
  });
  return {
    changed: t.defaultLayoutDeferred || !le(t.layout, a) || !wo(
      t.derivedItemConstraints,
      o
    ) || t.axisSize !== n,
    state: {
      defaultLayoutDeferred: !1,
      derivedItemConstraints: o,
      axisSize: n,
      layout: a,
      requestedAxisSize: r,
      requestedLayout: i,
      separatorToItems: t.separatorToItems
    }
  };
}
function Mt({
  axisId: e,
  itemId: t
}) {
  const n = () => {
    const l = be();
    for (const [c, d] of l)
      if (c.id === e)
        return { axis: c, ...d };
    throw Error(`Group ${e} not found`);
  }, o = () => {
    const l = n().derivedItemConstraints.find(
      (c) => c.itemId === t
    );
    if (l !== void 0)
      return l;
    throw Error(`Panel constraints not found for Panel ${t}`);
  }, r = () => {
    const { axis: l, ...c } = n(), d = Pt({ axis: l, axisState: c }), p = d?.state ?? c, g = p.derivedItemConstraints.find(
      (f) => f.itemId === t
    );
    if (g === void 0)
      throw Error(`Panel constraints not found for Panel ${t}`);
    const m = p.layout[t];
    if (m === void 0)
      throw Error(`Layout not found for Panel ${t}`);
    return {
      axis: l,
      changed: d?.changed === !0,
      itemConstraints: g,
      itemSize: m,
      state: p
    };
  }, i = () => {
    const l = n().axis.items.find((c) => c.id === t);
    if (l !== void 0)
      return l;
    throw Error(`Layout not found for Panel ${t}`);
  }, s = () => {
    const l = n().layout[t];
    if (l !== void 0)
      return l;
    throw Error(`Layout not found for Panel ${t}`);
  }, a = ({
    allowPartialSize: l = !1,
    nextSize: c,
    items: d,
    prevLayout: p,
    derivedItemConstraints: g
  }) => {
    const m = p[t], f = d.findIndex((h) => h.id === t), y = f === 0, S = f === d.length - 1;
    if (S && c < m && (y || d.slice(0, f).every((h, I) => {
      const L = g[I];
      return L?.collapsible && W(L.collapsedSize, p[L.itemId]);
    }))) {
      const h = d.slice(0, f).reduce((I, L) => I + p[L.id], 0);
      return {
        ...p,
        [t]: Q(100 - h)
      };
    }
    const z = g[f], x = Ee({
      overrideDisabledItems: !0,
      itemConstraints: z,
      prevSize: m,
      size: c
    }), R = { ...p };
    let k = x - m;
    for (const h of y ? [1] : S ? [-1] : [1, -1])
      for (let I = f + h; I >= 0 && I < d.length && !W(k, 0); I += h) {
        const L = g[I], M = R[L.itemId];
        if (st(L, R) || (k > 0 ? oe(M, L.minSize) <= 0 : oe(M, L.maxSize) >= 0))
          continue;
        const T = k > 0 ? Math.max(L.minSize, M - k) : Math.min(L.maxSize, M - k);
        k -= M - T, R[L.itemId] = Q(T);
      }
    if (R[t] = Q(x - k), W(k, 0) || l && oe(R[t], z.minSize) >= 0)
      return R;
    const O = (h, I, L) => {
      const M = h === "after" ? 1 : -1, T = (G) => {
        for (let N = f + M; N >= 0 && N < d.length; N += M) {
          const P = g[N];
          if (!st(P, I) && G(P))
            return N;
        }
      }, E = T(
        ({ itemId: G, maxSize: N, minSize: P }) => L > 0 ? oe(I[G], P) > 0 : oe(I[G], N) < 0
      ) ?? T(() => !0) ?? f + M;
      return Ve({
        delta: h === "after" ? L : -L,
        initialLayout: I,
        itemConstraints: g,
        pivotIndices: h === "after" ? [f, E] : [E, f],
        prevLayout: I,
        trigger: "imperative-api"
      });
    };
    if (y || S)
      return O(
        S ? "before" : "after",
        p,
        c - m
      );
    const b = (h) => {
      const I = O(h, p, c - m), L = c - I[t];
      return W(L, 0) ? I : O(
        h === "after" ? "before" : "after",
        I,
        L
      );
    }, w = b("after");
    if (Vt({
      itemConstraints: g,
      itemId: t,
      nextLayout: w,
      prevLayout: p
    })) {
      const h = b("before");
      if (!Vt({
        itemConstraints: g,
        itemId: t,
        nextLayout: h,
        prevLayout: p
      }))
        return h;
    }
    return w;
  }, u = (l, { axis: c, changed: d, itemSize: p, state: g }, { allowPartialSize: m = !1 } = {}) => {
    if (l === p)
      return;
    const { derivedItemConstraints: f, axisSize: y, layout: S } = g, v = a({
      allowPartialSize: m,
      nextSize: l,
      items: c.items,
      prevLayout: S,
      derivedItemConstraints: f
    }), z = ze({
      layout: v,
      itemConstraints: f
    });
    (d || !le(S, z)) && ge(c, {
      ...g,
      layout: z,
      requestedAxisSize: y,
      requestedLayout: z
    });
  };
  return {
    collapse: () => {
      const l = r(), { collapsible: c, collapsedSize: d } = l.itemConstraints, { mutableValues: p } = i(), g = l.itemSize;
      c && g !== d && (p.expandToSize = g, u(d, l));
    },
    expand: () => {
      const l = r(), { collapsible: c, collapsedSize: d, minSize: p } = l.itemConstraints, { mutableValues: g } = i(), m = l.itemSize;
      if (c && m === d) {
        let f = g.expandToSize ?? p;
        f === 0 && (f = 1), u(f, l, { allowPartialSize: !0 });
      }
    },
    getSize: () => {
      const { axis: l } = n(), c = s(), { element: d } = i(), p = l.layoutStrategy ? l.layoutStrategy.getItemSizeInPixels(t) : l.orientation === "horizontal" ? d.offsetWidth : d.offsetHeight;
      return {
        asPercentage: c,
        inPixels: p
      };
    },
    isCollapsed: () => {
      const { collapsible: l, collapsedSize: c } = o(), d = s();
      return l && W(c, d);
    },
    resize: (l) => {
      const c = r(), { axisSize: d } = c.state, { element: p } = i(), g = Me({
        axisSize: d,
        itemElement: p,
        styleProp: l
      }), m = Q(g / d * 100);
      u(m, c);
    }
  };
}
function st({ collapsedSize: e, collapsible: t, itemId: n }, o) {
  return t && W(o[n], e);
}
function Vt({
  itemConstraints: e,
  itemId: t,
  nextLayout: n,
  prevLayout: o
}) {
  return e.some(
    (r) => r.itemId !== t && st(r, o) !== st(r, n)
  );
}
function jt(e) {
  if (e.defaultPrevented)
    return;
  const t = be();
  Et(
    e,
    t,
    e.currentTarget
  ).forEach((o) => {
    if (o.separator && !o.separator.disableDoubleClick) {
      const r = o.items.find(
        (i) => i.constraintProps.defaultSize !== void 0
      );
      if (r) {
        const i = r.constraintProps.defaultSize, s = Mt({
          axisId: o.axis.id,
          itemId: r.id
        });
        s && i !== void 0 && (s.resize(i), e.preventDefault());
      }
    }
  });
}
function In(e) {
  const t = be();
  for (const [n] of t)
    if (n.separators.some(
      (o) => o.element === e
    ))
      return n;
  throw Error("Could not find parent Group for separator element");
}
function He({
  axisId: e
}) {
  const t = () => {
    const n = be();
    for (const [o, r] of n)
      if (o.id === e)
        return { axis: o, ...r };
    throw Error(`Could not find Group with id "${e}"`);
  };
  return {
    getLayout() {
      const { defaultLayoutDeferred: n, layout: o } = t();
      return n ? {} : o;
    },
    setLayout(n) {
      const { axis: o, ...r } = t(), i = Pt({ axis: o, axisState: r }), s = i?.state ?? r, {
        defaultLayoutDeferred: a,
        derivedItemConstraints: u,
        axisSize: l,
        layout: c
      } = s, d = ze({
        layout: n,
        itemConstraints: u
      });
      return a ? c : ((i?.changed || !le(c, d)) && ge(o, {
        ...s,
        layout: d,
        requestedAxisSize: l,
        requestedLayout: d
      }), d);
    }
  };
}
function Re(e, t) {
  const n = In(e), o = ue(n.id, !0), r = n.separators.find(
    (d) => d.element === e
  );
  F(r, "Matching separator not found");
  const i = o.separatorToItems.get(r);
  F(i, "Matching panels not found");
  const s = i.map((d) => n.items.indexOf(d)), u = He({ axisId: n.id }).getLayout(), l = Ve({
    delta: t,
    initialLayout: u,
    itemConstraints: o.derivedItemConstraints,
    pivotIndices: s,
    prevLayout: u,
    trigger: "keyboard"
  }), c = ze({
    layout: l,
    itemConstraints: o.derivedItemConstraints
  });
  le(u, c) || ge(
    n,
    {
      defaultLayoutDeferred: o.defaultLayoutDeferred,
      derivedItemConstraints: o.derivedItemConstraints,
      axisSize: o.axisSize,
      layout: c,
      requestedAxisSize: o.axisSize,
      requestedLayout: c,
      separatorToItems: o.separatorToItems
    },
    // Keyboard resizes (arrow keys, Home/End, Enter collapse/expand) originate
    // from a real DOM event on the separator, so they are user interactions
    // just like pointer drags. This function is only reached from
    // onDocumentKeyDown. See #716.
    { isUserInteraction: !0 }
  );
}
function Lo(e) {
  return e.separators.filter((t) => !t.disabled);
}
function Ut(e) {
  if (e.defaultPrevented)
    return;
  const t = e.currentTarget, n = In(t);
  if (!(n.disabled || n.separators.find(
    (r) => r.element === t
  )?.disabled))
    switch (e.key) {
      case "ArrowDown": {
        e.preventDefault(), n.orientation === "vertical" && Re(t, 5);
        break;
      }
      case "ArrowLeft": {
        e.preventDefault(), n.orientation === "horizontal" && Re(t, -5);
        break;
      }
      case "ArrowRight": {
        e.preventDefault(), n.orientation === "horizontal" && Re(t, 5);
        break;
      }
      case "ArrowUp": {
        e.preventDefault(), n.orientation === "vertical" && Re(t, -5);
        break;
      }
      case "End": {
        e.preventDefault(), Re(t, 100);
        break;
      }
      case "Enter": {
        e.preventDefault();
        const r = ue(n.id, !0), { derivedItemConstraints: i, layout: s, separatorToItems: a } = r, u = n.separators.find(
          (p) => p.element === t
        );
        F(u, "Matching separator not found");
        const l = a.get(u);
        F(l, "Matching panels not found");
        const c = l[0], d = i.find(
          (p) => p.itemId === c.id
        );
        if (F(d, "Panel metadata not found"), d.collapsible) {
          const p = s[c.id], g = d.collapsedSize === p ? n.mutableState.expandedItemSizes[c.id] ?? d.minSize : d.collapsedSize;
          Re(t, g - p);
        }
        break;
      }
      case "F6": {
        e.preventDefault();
        const r = Lo(n).map(
          (u) => u.element
        ), i = r.findIndex(
          (u) => u === e.currentTarget
        );
        F(i !== -1, "Index not found");
        const s = e.shiftKey ? i > 0 ? i - 1 : r.length - 1 : i + 1 < r.length ? i + 1 : 0;
        r[s].focus({
          preventScroll: !0
        });
        break;
      }
      case "Home": {
        e.preventDefault(), Re(t, -100);
        break;
      }
    }
}
function $e(e, t) {
  const n = ae();
  if (n.state !== "active" || !ut(n, e, t))
    return !1;
  const o = be();
  ct();
  try {
    n.previewLayoutMap.forEach((r, i) => {
      const s = o.get(i);
      i.resizePreviewMode === "separator" && s && !le(r, s.layout) && ge(i, {
        ...s,
        layout: r,
        requestedAxisSize: s.axisSize,
        requestedLayout: r
      });
    }), Ct(), he({
      cursorFlags: 0,
      state: "inactive"
    }), n.hitRegions.forEach((r) => {
      if (!o.has(r.axis))
        return;
      const i = ue(r.axis.id, !0);
      ge(r.axis, i, {
        isUserInteraction: !0
      });
    });
  } finally {
    lt();
  }
  return !0;
}
function Wt(e) {
  const t = ae();
  if (t.state !== "active")
    return;
  const n = t.hitRegions.flatMap(
    ({ separator: o }) => o ? [o.element] : []
  );
  !n.some((o) => o === e.target) || n.some(
    (o) => o.hasPointerCapture?.(e.pointerId)
  ) || $e(e.currentTarget, e);
}
function Yt(e) {
  $e(e.currentTarget, e);
}
function Io(e, t) {
  const { element: n, orientation: o, items: r } = e, i = n.getBoundingClientRect(), s = o === "horizontal";
  return zt({
    expandHitTargets: !1,
    axis: e,
    includeDisabled: !0
  }).map(({ items: u, rect: l, separator: c }, d) => {
    const p = r.indexOf(u[0]), g = s ? l.left + l.width / 2 : l.top + l.height / 2;
    return {
      active: t.some((f) => {
        if (f.axis !== e || f.items[0] !== u[0])
          return !1;
        if (c || f.separator)
          return f.separator === c;
        const y = s ? f.rect.left + f.rect.width / 2 : f.rect.top + f.rect.height / 2;
        return W(g, y);
      }),
      axis: e,
      key: c ? `separator-${c.id}` : `panel-${u[0].id}-${d}`,
      offset: 0,
      itemIndex: p,
      rect: new DOMRect(
        (s && !c ? g : l.left) - i.left - n.clientLeft + n.scrollLeft,
        (!s && !c ? g : l.top) - i.top - n.clientTop + n.scrollTop,
        s && !c ? 0 : l.width,
        !s && !c ? 0 : l.height
      ),
      separator: c
    };
  });
}
function Xt(e) {
  if (e.defaultPrevented)
    return;
  if (e.pointerType === "mouse" && e.button > 0)
    return;
  const t = ae();
  if (t.state === "active") {
    vn(t, e) && $e(
      t.ownerDocument,
      t
    );
    return;
  }
  const n = be(), o = Et(
    e,
    n,
    e.currentTarget
  );
  if (o.length === 0)
    return;
  const r = /* @__PURE__ */ new Map();
  let i = !1;
  o.forEach((a) => {
    a.separator && (i || (i = !0, a.separator.element.focus({
      // @ts-expect-error https://developer.mozilla.org/en-US/docs/Web/API/HTMLElement/focus#browser_compatibility
      focusVisible: !1,
      preventScroll: !0
    })));
    const u = n.get(a.axis);
    u && r.set(a.axis, u.layout);
  });
  const s = Array.from(r.keys()).flatMap(
    (a) => a.resizePreviewMode === "separator" ? Io(a, o) : []
  );
  he({
    cursorFlags: 0,
    didPointerMove: !1,
    hitRegions: o,
    initialLayoutMap: r,
    ownerDocument: e.currentTarget,
    pointerDownAtPoint: { x: e.clientX, y: e.clientY },
    pointerId: e.pointerId,
    pointerType: e.pointerType,
    previewLayoutMap: new Map(r),
    previews: s,
    state: "active"
  }), e.preventDefault();
}
function Kt(e) {
  const t = e.currentTarget, n = be(), o = ae();
  switch (o.state) {
    case "active": {
      if (!ut(o, t, e))
        return;
      Rt({
        commit: !1,
        event: e,
        hitRegions: o.hitRegions,
        initialLayoutMap: o.initialLayoutMap,
        mountedAxes: n,
        prevCursorFlags: o.cursorFlags
      });
    }
  }
}
function Jt(e) {
  if (e.defaultPrevented)
    return;
  const t = e.currentTarget, n = ae(), o = be();
  switch (n.state) {
    case "active": {
      if (!ut(n, t, e))
        return;
      if (
        // Skip this check for "pointerleave" events, else Firefox triggers a false positive (see #514)
        e.buttons === 0
      ) {
        $e(t, e);
        return;
      }
      for (const r of n.hitRegions)
        if (r.separator) {
          const { element: i } = r.separator;
          i.isConnected && !i.hasPointerCapture?.(e.pointerId) && i.setPointerCapture?.(e.pointerId);
        }
      Rt({
        commit: !1,
        event: e,
        hitRegions: n.hitRegions,
        initialLayoutMap: n.initialLayoutMap,
        mountedAxes: o,
        pointerDownAtPoint: n.pointerDownAtPoint,
        prevCursorFlags: n.cursorFlags
      });
      break;
    }
    default: {
      const r = Et(
        e,
        o,
        t
      );
      r.length === 0 ? n.state !== "inactive" && he({
        cursorFlags: 0,
        state: "inactive"
      }) : he({
        cursorFlags: 0,
        hitRegions: r,
        ownerDocument: t,
        state: "hover"
      });
      break;
    }
  }
}
function Zt(e) {
  if (e.relatedTarget instanceof HTMLIFrameElement) {
    const t = ae();
    switch (t.state) {
      case "hover":
        t.ownerDocument === e.currentTarget && he({
          cursorFlags: 0,
          state: "inactive"
        });
    }
  }
}
function Qt(e) {
  e.defaultPrevented || e.pointerType === "mouse" && e.button > 0 || bn(e.currentTarget, e);
}
function en(e) {
  e.target === e.currentTarget && $e(e.currentTarget.document);
}
function tn(e) {
  $e(e.currentTarget.document);
}
function Co(e, t, n) {
  if (!n[0])
    return;
  const r = e.items.find((l) => l.element === t);
  if (!r || !r.onResize)
    return;
  const i = Ke({ axis: e }), s = e.orientation === "horizontal" ? r.element.offsetWidth : r.element.offsetHeight, a = r.mutableValues.prevSize, u = {
    asPercentage: Q(s / i * 100),
    inPixels: s
  };
  r.mutableValues.prevSize = u, r.onResize(u, r.id, a);
}
const Pe = /* @__PURE__ */ new Map();
let nn = !1;
function Cn(e) {
  let t = !0;
  F(
    e.element.ownerDocument.defaultView,
    "Cannot register an unmounted Group"
  );
  const n = e.element.ownerDocument.defaultView.ResizeObserver, o = /* @__PURE__ */ new Set(), r = /* @__PURE__ */ new Set(), i = new n((m) => {
    let f;
    ct();
    try {
      for (const y of m) {
        const { borderBoxSize: S, target: v } = y;
        if (v === e.element) {
          if (t) {
            const z = ue(e.id);
            if (!z)
              break;
            const x = Pt({ axis: e, axisState: z });
            if (!x)
              break;
            if (!x.changed)
              continue;
            ge(e, x.state);
          }
        } else
          try {
            Co(e, v, S);
          } catch (z) {
            f ??= { error: z };
          }
      }
    } finally {
      lt();
    }
    if (f)
      throw f.error;
  });
  i.observe(e.element), e.items.forEach((m) => {
    F(
      !o.has(m.id),
      `Panel ids must be unique; id "${m.id}" was used more than once`
    ), o.add(m.id), m.onResize && i.observe(m.element);
  });
  const s = Ke({ axis: e }), a = Lt(e, s), u = wn({
    axis: e,
    itemConstraints: a
  }), l = ze({
    layout: u,
    itemConstraints: a
  }), c = e.element.ownerDocument, d = c.defaultView;
  Pe.set(
    c,
    (Pe.get(c) ?? 0) + 1
  );
  const p = /* @__PURE__ */ new Map();
  return zt({ axis: e, includeDisabled: !0 }).forEach((m) => {
    m.separator && p.set(m.separator, m.items);
  }), ge(e, {
    defaultLayoutDeferred: s === 0,
    derivedItemConstraints: a,
    axisSize: s,
    layout: l,
    requestedAxisSize: s,
    requestedLayout: Ln({
      itemIds: e.items.map(({ id: m }) => m),
      layout: u
    }),
    separatorToItems: p
  }), e.separators.forEach((m) => {
    F(
      !r.has(m.id),
      `Separator ids must be unique; id "${m.id}" was used more than once`
    ), r.add(m.id), m.element.addEventListener("keydown", Ut);
  }), nn || (nn = !0, xt(Qn)), Pe.get(c) === 1 && (c.addEventListener("contextmenu", _t, !0), c.addEventListener("dblclick", jt, !0), c.addEventListener(
    "lostpointercapture",
    Wt,
    !0
  ), c.addEventListener(
    "pointercancel",
    Yt,
    !0
  ), c.addEventListener("pointerdown", Xt, !0), c.addEventListener("pointerleave", Kt), c.addEventListener("pointermove", Jt), c.addEventListener("pointerout", Zt), c.addEventListener("pointerup", Qt, !0), d?.addEventListener("blur", en), d?.addEventListener("pagehide", tn)), function() {
    t = !1, Pe.set(
      c,
      Math.max(0, (Pe.get(c) ?? 0) - 1)
    ), ao(e), Kn(e), e.separators.forEach((f) => {
      f.element.removeEventListener("keydown", Ut);
    }), Pe.get(c) || (c.removeEventListener(
      "contextmenu",
      _t,
      !0
    ), c.removeEventListener(
      "dblclick",
      jt,
      !0
    ), c.removeEventListener(
      "lostpointercapture",
      Wt,
      !0
    ), c.removeEventListener(
      "pointercancel",
      Yt,
      !0
    ), c.removeEventListener(
      "pointerdown",
      Xt,
      !0
    ), c.removeEventListener("pointerleave", Kt), c.removeEventListener("pointermove", Jt), c.removeEventListener("pointerout", Zt), c.removeEventListener("pointerup", Qt, !0), d?.removeEventListener("blur", en), d?.removeEventListener("pagehide", tn)), i.disconnect();
  };
}
function Rn({
  derivedItemConstraints: e,
  axis: t,
  layout: n,
  prevLayout: o,
  requestedLayout: r
}) {
  const i = t.items.map(({ id: s }) => s).join(",");
  t.mutableState.layouts[i] = r, o && e.forEach((s) => {
    if (s.collapsible) {
      const a = W(
        s.collapsedSize,
        n[s.itemId]
      ), u = W(
        s.collapsedSize,
        o[s.itemId]
      );
      a && !u && (t.mutableState.expandedItemSizes[s.itemId] = o[s.itemId]);
    }
  });
}
function En() {
  const [e, t] = ve({}), n = se(() => t({}), []);
  return [e, n];
}
function dt(e) {
  const t = H({ ...e });
  return re(() => {
    for (const n in e)
      t.current[n] = e[n];
  }, [e]), t.current;
}
const kt = Ue(null), Pn = Ue(null);
function Ro({
  layout: e,
  itemConstraints: t,
  itemId: n,
  itemIndex: o
}) {
  let r, i;
  const s = e[n], a = t.find(
    (u) => u.itemId === n
  );
  if (a) {
    const u = a.maxSize, l = a.collapsible ? a.collapsedSize : a.minSize, c = [o, o + 1];
    i = ze({
      layout: Ve({
        delta: l - s,
        initialLayout: e,
        itemConstraints: t,
        pivotIndices: c,
        prevLayout: e
      }),
      itemConstraints: t
    })[n], r = ze({
      layout: Ve({
        delta: u - s,
        initialLayout: e,
        itemConstraints: t,
        pivotIndices: c,
        prevLayout: e
      }),
      itemConstraints: t
    })[n];
  }
  return {
    valueControls: n,
    valueMax: r,
    valueMin: i,
    valueNow: s
  };
}
function Mn({
  attributes: e,
  axisId: t,
  axisOrientation: n,
  children: o,
  className: r,
  "data-testid": i,
  disableCursor: s,
  disabled: a,
  disableDoubleClick: u,
  elementRef: l,
  id: c,
  isSharedHitRegion: d,
  preview: p,
  registerSeparator: g,
  requiredStyle: m,
  style: f,
  styleDefaults: y,
  tabIndex: S = 0,
  updateSeparatorProps: v
}) {
  const z = dt({
    disabled: a,
    disableDoubleClick: u,
    children: o,
    className: r,
    preview: p,
    style: f
  }), [x, R] = ve({}), [k, O] = ve("inactive"), [b, w] = ve(!1), h = H(null), I = H(null), L = Ye(h, l), M = n === "horizontal" ? "vertical" : "horizontal";
  re(() => {
    const G = h.current;
    if (G !== null) {
      const N = {
        disabled: z.disabled,
        disableDoubleClick: z.disableDoubleClick,
        element: G,
        id: c,
        get children() {
          return z.children;
        },
        get className() {
          return z.className;
        },
        get preview() {
          return z.preview;
        },
        get style() {
          return z.style;
        }
      };
      I.current = N;
      const P = g(N), A = xt(
        (j) => {
          O(
            j.next.state !== "inactive" && j.next.hitRegions.some(
              (U) => U.separator === N || (d?.(U) ?? !1)
            ) ? j.next.state : "inactive"
          );
        }
      ), V = qe(
        t,
        (j) => {
          const { derivedItemConstraints: U, layout: de, separatorToItems: xe } = j.next, Le = xe.get(N);
          if (Le) {
            const Ie = Le[0], De = U.findIndex(
              (C) => C.itemId === Ie.id
            ), { layoutStrategy: ee } = j.axis;
            R({
              ...Ro({
                layout: de,
                itemConstraints: U,
                itemId: Ie.id,
                itemIndex: De
              }),
              // Items without DOM elements of their own (e.g. Grid tracks) control other elements
              ...ee?.getItemAriaControls && {
                valueControls: ee.getItemAriaControls(
                  Ie.id
                )
              }
            });
          }
        }
      );
      return () => {
        I.current = null, A(), V(), P();
      };
    }
  }, [t, c, d, g, z]), re(() => {
    const G = I.current;
    G && Xn(G);
  }, [p]), je(() => {
    v(c, { disabled: a, disableDoubleClick: u });
  }, [a, u, c, v]);
  let T;
  a && !s && (T = "not-allowed");
  let E;
  if (a)
    E = "disabled";
  else
    switch (k) {
      case "active": {
        E = "active";
        break;
      }
      default:
        b ? E = "focus" : E = k;
    }
  return /* @__PURE__ */ X(
    "div",
    {
      ...e,
      "aria-controls": x.valueControls,
      "aria-disabled": a || void 0,
      "aria-orientation": M,
      "aria-valuemax": x.valueMax,
      "aria-valuemin": x.valueMin,
      "aria-valuenow": x.valueNow,
      children: o,
      className: r,
      "data-separator": E,
      "data-testid": i,
      id: c,
      onBlur: () => w(!1),
      onFocus: () => w(!0),
      ref: L,
      role: "separator",
      style: {
        ...y,
        cursor: T,
        ...f,
        ...m,
        // Inform the browser that the library is handling touch events for this element
        // See github.com/bvaughn/react-resizable-panels/issues/662
        touchAction: "none"
      },
      tabIndex: a ? void 0 : S
    }
  );
}
function on({
  axis: e,
  children: t,
  className: n,
  crossSpan: o,
  crossStart: r,
  disabled: i,
  elementRef: s,
  index: a,
  style: u
}) {
  const l = Ae(void 0), {
    getDisableCursor: c,
    getResizeAxisId: d,
    gutters: p,
    registerSeparator: g,
    separatorPlacements: m,
    trackCounts: f,
    updateSeparatorProps: y
  } = dn(), S = d(e), v = e === "column" ? "row" : "column", z = f[v], x = !!i || !vt(a, f[e]);
  let R = !0;
  for (const h of m.values())
    if (h.axis === e && h.index === a && !h.disabled && h.crossStart < r) {
      R = !1;
      break;
    }
  const k = se(
    (h) => h.axis.id === S && h.axis.items.indexOf(h.items[1]) === a,
    [S, a]
  ), O = se(
    (h) => g(
      {
        axis: e,
        crossSpan: o,
        crossStart: r,
        disabled: !!i,
        index: a
      },
      h
    ),
    [e, o, r, i, a, g]
  ), b = Bn(a), w = Hn({
    crossCount: z,
    crossSpan: o,
    crossStart: r,
    hasGutters: p[v]
  });
  return /* @__PURE__ */ X(
    Mn,
    {
      axisId: S,
      axisOrientation: e === "column" ? "horizontal" : "vertical",
      className: n,
      disableCursor: c(),
      disabled: x,
      disableDoubleClick: void 0,
      elementRef: s,
      id: l,
      isSharedHitRegion: k,
      registerSeparator: O,
      requiredStyle: {
        gridColumn: e === "column" ? b : w,
        gridRow: e === "row" ? b : w
      },
      style: u,
      tabIndex: R ? 0 : -1,
      updateSeparatorProps: y,
      children: t
    }
  );
}
function kn(e) {
  const { children: t, className: n, disabled: o, elementRef: r, style: i } = e;
  return e.type === "column" ? /* @__PURE__ */ X(
    on,
    {
      axis: "column",
      className: n,
      crossSpan: e.rowSpan,
      crossStart: e.row ?? 0,
      disabled: o,
      elementRef: r,
      index: e.column,
      style: i,
      children: t
    }
  ) : /* @__PURE__ */ X(
    on,
    {
      axis: "row",
      className: n,
      crossSpan: e.columnSpan,
      crossStart: e.column ?? 0,
      disabled: o,
      elementRef: r,
      index: e.row,
      style: i,
      children: t
    }
  );
}
kn.displayName = "Gridline";
const ke = ["column", "row"], rn = { columns: {}, rows: {} };
function Eo({
  children: e,
  className: t,
  columns: n,
  defaultLayout: o,
  disableCursor: r,
  disabled: i,
  elementRef: s,
  gridRef: a,
  id: u,
  onLayoutChange: l,
  onLayoutChanged: c,
  resizeTargetMinimumSize: d = {
    coarse: 20,
    fine: 10
  },
  rows: p,
  style: g,
  ...m
}) {
  const f = Ae(u), y = se(
    (C) => C === "column" ? `${f}:columns` : `${f}:rows`,
    [f]
  ), S = sn(n, "columns"), v = sn(p, "rows"), z = (C) => C === "column" ? S : v, x = H({
    onLayoutChange: rn,
    onLayoutChanged: rn
  }), R = we((C) => {
    an(x.current.onLayoutChange, C) || (x.current.onLayoutChange = C, l?.(C));
  }), k = we(
    (C, $, B) => {
      an(x.current.onLayoutChanged, C) || (x.current.onLayoutChanged = C, c?.(C, { isUserInteraction: $, requestedLayout: B }));
    }
  ), O = H(null), b = Ye(O, s), [w, h] = En(), I = H({
    cells: [],
    column: { expandedItemSizes: {}, layouts: {} },
    loggedErrors: /* @__PURE__ */ new Set(),
    row: { expandedItemSizes: {}, layouts: {} },
    separators: /* @__PURE__ */ new Map()
  }), L = { column: !1, row: !1 };
  for (const C of I.current.separators.values())
    L[C.axis] = !0;
  Tn(e, L);
  const M = dt({
    defaultLayout: o,
    disableCursor: r,
    resizeTargetMinimumSize: d
  }), T = we(() => !!M.disableCursor), E = se(
    (C) => {
      const $ = I.current;
      return $.cells = [...$.cells, C], h(), () => {
        $.cells = $.cells.filter(
          (B) => B !== C
        ), h();
      };
    },
    [h]
  ), G = se(
    (C, $) => {
      const B = I.current;
      return B.separators = new Map(B.separators), B.separators.set($, C), h(), () => {
        B.separators = new Map(B.separators), B.separators.delete($), h();
      };
    },
    [h]
  ), N = se(
    (C, {
      disabled: $,
      disableDoubleClick: B
    }) => {
      for (const _ of I.current.separators.keys())
        _.id === C && (_.disabled = $, _.disableDoubleClick = B);
    },
    []
  ), P = I.current.separators, A = pe(
    () => ({
      getDisableCursor: T,
      getResizeAxisId: y,
      gutters: {
        column: L.column,
        row: L.row
      },
      id: f,
      registerCell: E,
      registerSeparator: G,
      separatorPlacements: P,
      trackCounts: {
        column: S.length,
        row: v.length
      },
      updateSeparatorProps: N
    }),
    [
      S.length,
      T,
      y,
      L.column,
      L.row,
      f,
      E,
      G,
      v.length,
      P,
      N
    ]
  ), V = H([]);
  re(() => {
    const C = O.current;
    if (C === null)
      return;
    const $ = I.current, B = Mo({
      cells: $.cells,
      separatorPlacements: $.separators,
      trackCounts: { column: S.length, row: v.length }
    });
    B.forEach((D) => {
      $.loggedErrors.has(D) || console.error(D);
    }), $.loggedErrors = new Set(B);
    const _ = (D) => Array.from($.separators.entries()).filter(([, J]) => J.axis === D).sort(([, J], [, ye]) => J.index - ye.index).map(([J]) => J), q = (D) => {
      const J = D === "column" ? S : v, ye = D === "column" ? v : S, Se = J.map((te) => te.id), Je = y(D), Ze = _(D), Ce = Ze.length > 0, Qe = J.map(
        ({ id: te, ...ie }, ne) => ({
          // Tracks don't have DOM elements of their own;
          // the Grid element is used to resolve relative size constraints (e.g. "em")
          element: C,
          id: Se[ne],
          idIsStable: !0,
          mutableValues: {
            expandToSize: void 0,
            prevSize: void 0
          },
          onResize: void 0,
          constraintProps: ie
        })
      ), Ge = () => {
        const te = [];
        if (Ce) {
          for (const [ie, ne] of $.separators)
            if (ne.axis === D && vt(ne.index, J.length)) {
              const Fe = ie.element.getBoundingClientRect();
              te[ne.index] = Math.max(
                te[ne.index] ?? 0,
                D === "column" ? Fe.width : Fe.height
              );
            }
        }
        return Un({
          axis: D,
          element: C,
          gutterSizes: te.filter((ie) => ie !== void 0),
          hasGutters: Ce,
          layout: ue(Je)?.layout,
          trackIds: Se
        });
      };
      let Ne;
      const mt = M.defaultLayout?.[D === "column" ? "columns" : "rows"];
      if (mt !== void 0 && Object.keys(mt).length === Se.length) {
        Ne = {};
        for (const te of Se) {
          const ie = mt[te];
          ie !== void 0 && (Ne[te] = ie);
        }
      }
      return {
        disabled: !!i,
        element: C,
        id: Je,
        layoutStrategy: {
          getItemAriaControls: (te) => {
            const ie = Se.indexOf(te);
            return $.cells.filter((ne) => {
              const Fe = D === "column" ? ne.column : ne.row, On = D === "column" ? ne.columnSpan : ne.rowSpan;
              return Fe <= ie && ie < Fe + On;
            }).map((ne) => ne.element.id).filter(Boolean).join(" ") || void 0;
          },
          calculateAvailableSize: () => Ge().availableSize,
          calculateHitRegions: ({
            expandHitTargets: te,
            axis: ie,
            includeDisabled: ne
          }) => jn({
            axis: D,
            cells: $.cells,
            crossTrackCount: ye.length,
            expandHitTargets: te,
            resizeAxis: ie,
            axisSize: Ge().availableSize,
            includeDisabled: ne,
            separatorPlacements: $.separators
          }),
          getItemSizeInPixels: (te) => Ge().trackSizes[Se.indexOf(te)] ?? 0
        },
        mutableState: {
          defaultLayout: Ne,
          disableCursor: !!M.disableCursor,
          expandedItemSizes: $[D].expandedItemSizes,
          layouts: $[D].layouts
        },
        orientation: D === "column" ? "horizontal" : "vertical",
        items: Qe,
        resizePreviewMode: "panel",
        get resizeTargetMinimumSize() {
          return M.resizeTargetMinimumSize;
        },
        separators: Ze
      };
    }, Z = ke.map(q);
    V.current = Z;
    const K = Z.map((D) => Cn(D)), ce = () => {
      const [D, J] = Z.map(
        (ye) => ue(ye.id)
      );
      if (!(!D || !J || D.defaultLayoutDeferred || J.defaultLayoutDeferred))
        return {
          layout: { columns: D.layout, rows: J.layout },
          requestedLayout: {
            columns: D.requestedLayout,
            rows: J.requestedLayout
          }
        };
    };
    {
      const D = ce();
      if (D)
        try {
          try {
            R(D.layout);
          } finally {
            k(
              D.layout,
              !1,
              D.requestedLayout
            );
          }
        } catch (J) {
          throw V.current = [], K.forEach((ye) => ye()), J;
        }
    }
    const Oe = Z.map(
      (D) => qe(D.id, (J) => {
        const {
          defaultLayoutDeferred: ye,
          derivedItemConstraints: Se,
          layout: Je,
          requestedLayout: Ze
        } = J.next;
        if (ye || Se.length === 0)
          return;
        Rn({
          derivedItemConstraints: Se,
          axis: D,
          layout: Je,
          prevLayout: J.prev?.layout,
          requestedLayout: Ze
        });
        const Ce = ce();
        if (Ce) {
          const Qe = ae(), Ge = Qe.state !== "active" || !Qe.hitRegions.some(
            (Ne) => Z.includes(Ne.axis)
          );
          try {
            R(Ce.layout);
          } finally {
            Ge && k(
              Ce.layout,
              J.isUserInteraction,
              Ce.requestedLayout
            );
          }
        }
      })
    );
    return () => {
      V.current = [], K.forEach((D) => D()), Oe.forEach((D) => D());
    };
  }, [
    S,
    i,
    y,
    f,
    k,
    R,
    v,
    w,
    M
  ]), je(() => {
    V.current.forEach((C) => {
      C.mutableState.defaultLayout = o?.[C.orientation === "horizontal" ? "columns" : "rows"], C.mutableState.disableCursor = !!r;
    });
  });
  const j = pe(() => {
    const C = () => {
      const [_, q] = ke.map(y);
      return !Te(_) || !Te(q) ? { columns: {}, rows: {} } : {
        columns: He({
          axisId: _
        }).getLayout(),
        rows: He({ axisId: q }).getLayout()
      };
    }, $ = (_) => _.map((q) => `"${q}"`).join(", "), B = (_, q) => {
      const Z = y(_), K = () => {
        const ce = Te(Z);
        return F(ce, `Grid "${f}" has not been mounted`), F(
          ce.items.some((Oe) => Oe.id === q),
          `Grid "${f}" no longer contains a ${_} with id "${q}"; ${_} ids are ${$(ce.items.map((Oe) => Oe.id))}`
        ), Mt({ axisId: Z, itemId: q });
      };
      return {
        collapse: () => K().collapse(),
        expand: () => K().expand(),
        getSize: () => K().getSize(),
        isCollapsed: () => K().isCollapsed(),
        resize: (ce) => K().resize(ce)
      };
    };
    return {
      getLayout: C,
      getTrackById: (_, q) => {
        const Z = _ === "column" ? S : v, K = `${q}`;
        return F(
          Z.some((ce) => ce.id === K),
          `Grid "${f}" does not contain a ${_} with id "${K}"; ${_} ids are ${$(Z.map((ce) => ce.id))}`
        ), B(_, K);
      },
      getTrackByIndex: (_, q) => {
        const Z = _ === "column" ? S : v, K = Number.isInteger(q) ? Z[q] : void 0;
        return F(
          K,
          `Grid "${f}" does not contain a ${_} at index ${q}; it has ${Z.length} ${_}s`
        ), B(_, K.id);
      },
      setLayout: (_) => (ke.forEach((q) => {
        const Z = _[q === "column" ? "columns" : "rows"], K = y(q);
        Z && Te(K) && He({ axisId: K }).setLayout(Z);
      }), C())
    };
  }, [S, y, f, v]);
  St(a, () => j, [j]);
  const U = H(j);
  re(() => {
    U.current = j;
  });
  const de = pe(
    () => ({
      getLayout: () => U.current.getLayout(),
      getTrackById: (C, $) => U.current.getTrackById(C, $),
      getTrackByIndex: (C, $) => U.current.getTrackByIndex(C, $),
      id: f,
      setLayout: (C) => U.current.setLayout(C)
    }),
    [f]
  ), xe = () => ke.map((C) => {
    const $ = ue(y(C)), B = z(C);
    return ($ && !$.defaultLayoutDeferred ? B.map((q) => `minmax(0, ${$.layout[q.id] ?? 1}fr)`) : Po(
      B,
      o?.[C === "column" ? "columns" : "rows"]
    )).join(L[C] ? " auto " : " ");
  }).join("|"), Le = se(
    (C) => {
      const $ = ke.map(
        (B) => qe(y(B), C)
      );
      return () => $.forEach((B) => B());
    },
    [y]
  ), Ie = at(
    Le,
    xe,
    xe
  ), [De, ee] = Ie.split("|");
  return /* @__PURE__ */ X(un.Provider, { value: A, children: /* @__PURE__ */ X(
    "div",
    {
      ...m,
      className: t,
      "data-grid": !0,
      "data-testid": Xe(m, u),
      id: f,
      ref: b,
      style: {
        height: "100%",
        width: "100%",
        overflow: "hidden",
        ...g,
        display: "grid",
        gridTemplateColumns: De,
        gridTemplateRows: ee,
        // Inform the browser that the library is handling touch events for this element
        // NOTE This is not an inherited style
        touchAction: "none"
      },
      children: /* @__PURE__ */ X(kt.Provider, { value: null, children: /* @__PURE__ */ X(Pn.Provider, { value: de, children: e }) })
    }
  ) });
}
Eo.displayName = "Grid";
function sn(e, t) {
  const n = JSON.stringify(e);
  return pe(() => {
    const o = typeof e == "number" ? Array.from({ length: e }, (r, i) => ({ id: `${i}` })) : e.map((r, i) => ({
      ...r,
      id: `${r.id ?? i}`
    }));
    return F(
      o.length > 0,
      `Grid must have at least one track; "${t}" is ${n}`
    ), o;
  }, [n]);
}
function Tn(e, t) {
  Fn.forEach(e, (n) => {
    if (ln(n))
      if (n.type === kn) {
        const { type: o } = n.props;
        o === "column" ? t.column = !0 : o === "row" && (t.row = !0);
      } else // Fragments
      typeof n.type == "symbol" && n.props !== null && typeof n.props == "object" && "children" in n.props && Tn(n.props.children, t);
  });
}
function Po(e, t) {
  if (t)
    return e.map(
      (i) => `minmax(0, ${t[i.id] ?? 1}fr)`
    );
  const n = e.map(
    (i) => i.defaultSize === void 0 ? void 0 : fn(i.defaultSize)
  );
  let o = 100, r = 0;
  return n.forEach((i) => {
    i === void 0 ? r++ : i[1] === "%" && (o -= i[0]);
  }), n.map((i) => {
    if (i === void 0)
      return `minmax(0, ${Math.max(0, o) / Math.max(1, r)}fr)`;
    const [s, a] = i;
    return a === "%" ? `minmax(0, ${s}fr)` : `${s}${a}`;
  });
}
function Mo({
  cells: e,
  separatorPlacements: t,
  trackCounts: n
}) {
  const o = [];
  for (const a of e)
    ke.forEach((u) => {
      const [l, c] = u === "column" ? [a.column, a.columnSpan] : [a.row, a.rowSpan], d = n[u];
      (!Number.isInteger(l) || !Number.isInteger(c) || l < 0 || c < 1 || l + c > d) && o.push(
        `Invalid Cell ${u} placement (${u}: ${l}, ${u}Span: ${c}); Grid has ${d} ${u}s`
      );
    });
  const r = ({
    axis: a,
    crossSpan: u,
    crossStart: l,
    disabled: c,
    index: d
  }) => {
    const p = a === "column" ? "row" : "column";
    let g = `type="${a}" ${a}={${d}}`;
    return (l !== 0 || u !== void 0) && (g += ` ${p}={${l}}`), u !== void 0 && (g += ` ${p}Span={${u}}`), c && (g += " disabled"), `<Gridline ${g} />`;
  }, i = Array.from(t.values()).filter(
    (a) => {
      const { axis: u, crossSpan: l, crossStart: c, index: d } = a, p = u === "column" ? "row" : "column", g = n[u], m = n[p];
      let f = !0;
      return vt(d, g) || (o.push(
        `Invalid ${u} Gridline ${u} (${d}); must be between 1 and ${g - 1}`
      ), f = !1), !Number.isInteger(c) || c < 0 || c >= m ? (o.push(
        `Invalid ${u} Gridline ${p} (${c}); must be between 0 and ${m - 1}`
      ), f = !1) : l !== void 0 && (!Number.isInteger(l) || l < 1 || c + l > m) && (o.push(
        `Invalid ${u} Gridline ${p}Span (${l}); Grid has ${m} ${p}s`
      ), f = !1), f;
    }
  ), s = (a) => {
    const u = n[a.axis === "column" ? "row" : "column"];
    return [
      a.crossStart,
      a.crossSpan === void 0 ? u : a.crossStart + a.crossSpan
    ];
  };
  return i.forEach((a, u) => {
    const { axis: l, index: c } = a, [d, p] = s(a);
    for (const g of e) {
      const [m, f, y, S] = l === "column" ? [g.column, g.columnSpan, g.row, g.rowSpan] : [g.row, g.rowSpan, g.column, g.columnSpan];
      if (m < c && c < m + f && y < p && d < y + S) {
        const v = l === "column" ? "row" : "column";
        o.push(
          `${r(a)} overlaps a Cell that spans across it (${l}: ${m}, ${l}Span: ${f}, ${v}: ${y}, ${v}Span: ${S}); use the ${v} and ${v}Span props to render gridlines beside the Cell instead`
        );
      }
    }
    for (const g of i.slice(u + 1))
      if (g.axis === l && g.index === c) {
        const [m, f] = s(g);
        m < p && d < f && o.push(`${r(a)} overlaps ${r(g)}`);
      }
  }), o;
}
function an(e, t) {
  return le(e.columns, t.columns) && le(e.rows, t.rows);
}
function Ko({
  id: e,
  onlySaveAfterUserInteractions: t,
  storage: n = localStorage
}) {
  const o = `react-resizable-panels:grid:${e}`, r = at(
    ko,
    () => n.getItem(o),
    () => n.getItem(o)
  ), i = pe(() => {
    if (r)
      try {
        const a = JSON.parse(r);
        if (a !== null && typeof a == "object")
          return a;
      } catch {
      }
  }, [r]), s = se(
    (a, u) => {
      if (!(t && !u.isUserInteraction))
        try {
          n.setItem(
            o,
            JSON.stringify(u.requestedLayout ?? a)
          );
        } catch (l) {
          console.error(l);
        }
    },
    [t, n, o]
  );
  return {
    /**
     * Pass this value to `Grid` as the `defaultLayout` prop.
     */
    defaultLayout: i,
    /**
     * Attach this callback on the `Grid` as the `onLayoutChanged` prop.
     */
    onLayoutChanged: s
  };
}
function ko() {
  return function() {
  };
}
function Jo() {
  return ve(null);
}
function Zo() {
  return H(null);
}
function Qo() {
  return We(Pn);
}
function To(e) {
  for (let t = 1; t < e.length; t++)
    if (wt(e[t - 1], e[t]) > 0)
      return !1;
  return !0;
}
function Ao({
  separator: e
}) {
  const { element: t } = e, n = H(null);
  return re(() => {
    const o = t.cloneNode(!0), r = [t, ...t.querySelectorAll("*")], i = [o, ...o.querySelectorAll("*")], s = t.ownerDocument.defaultView;
    return r.forEach((a, u) => {
      const l = i[u];
      if (l instanceof s.HTMLElement || l instanceof s.SVGElement) {
        const c = s.getComputedStyle(a);
        for (let d = 0; d < c.length; d++) {
          const p = c.item(d);
          l.style.setProperty(
            p,
            c.getPropertyValue(p)
          );
        }
      }
      l.removeAttribute("data-testid"), l.removeAttribute("id");
    }), Object.assign(o.style, {
      boxSizing: "border-box",
      height: "100%",
      margin: "0",
      position: "static",
      transform: "none",
      width: "100%"
    }), n.current.appendChild(o), () => o.remove();
  }, [t]), /* @__PURE__ */ X(
    "div",
    {
      ref: n,
      style: {
        height: "100%",
        opacity: 0.65,
        pointerEvents: "none",
        width: "100%"
      }
    }
  );
}
function ft() {
  const e = We(kt);
  return F(
    e,
    "Group Context not found; did you render a Panel or Separator outside of a Group?"
  ), e;
}
function An(e) {
  const { registerOverlay: t } = ft(), n = H(e);
  zn(n.current, e) || (n.current = e);
  const o = n.current;
  return re(
    () => t(o),
    [t, o]
  ), null;
}
An.displayName = "SeparatorOverlay";
function $o({
  active: e,
  orientation: t,
  style: n,
  ...o
}) {
  let r;
  switch (t) {
    case "horizontal": {
      r = {
        height: "100%",
        minWidth: "1px"
      };
      break;
    }
    case "vertical": {
      r = {
        minHeight: "1px",
        width: "100%"
      };
      break;
    }
  }
  return /* @__PURE__ */ X(
    "div",
    {
      ...o,
      "data-separator-overlay": e ? "active" : "inactive",
      style: {
        ...r,
        ...n,
        flexShrink: 0,
        pointerEvents: "none"
      }
    }
  );
}
function Do({
  overlay: e,
  preview: t
}) {
  const { axis: n, offset: o, rect: r, separator: i } = t, s = n.orientation === "horizontal";
  let a = i?.preview, u = e;
  return ln(a) && a.type === An && (u = a.props, a = void 0), a == null && (u ? a = /* @__PURE__ */ X(
    $o,
    {
      ...u,
      active: t.active,
      orientation: n.orientation
    }
  ) : i && (a = /* @__PURE__ */ X(Ao, { separator: i }))), /* @__PURE__ */ X(
    "div",
    {
      "aria-hidden": "true",
      "data-resize-preview": !0,
      inert: !0,
      style: {
        height: r.height,
        left: r.left,
        pointerEvents: "none",
        position: "absolute",
        top: r.top,
        transform: s ? `translateX(${o}px)` : `translateY(${o}px)`,
        width: r.width
      },
      children: a
    }
  );
}
function Oo(e) {
  return Array.from(e).sort(wt);
}
const $n = Ue(null);
function Go(e, t) {
  const n = H({
    getLayout: () => ({}),
    setLayout: Jn
  });
  return St(t, () => n.current, []), re(() => {
    Object.assign(
      n.current,
      He({ axisId: e })
    );
  }), n;
}
function No({
  groupId: e,
  resizePreviewMode: t
}) {
  const [n, o] = ve([]), r = H(n);
  return re(() => {
    const i = (a) => {
      const u = Te(e), l = t === "separator" && a.state === "active" ? a.previews.filter(
        (d) => d.axis === u && (d.active || !W(d.offset, 0))
      ) : [], c = r.current;
      c.length === l.length && l.every(
        (d, p) => d === c[p]
      ) || (r.current = l, o(l));
    };
    if (t !== "separator") {
      i(ae());
      return;
    }
    const s = xt(
      ({ next: a }) => i(a)
    );
    return i(ae()), s;
  }, [e, t]), n;
}
function Fo({
  children: e,
  className: t,
  defaultLayout: n,
  disableCursor: o,
  disabled: r,
  elementRef: i,
  groupRef: s,
  id: a,
  onLayoutChange: u,
  onLayoutChanged: l,
  orientation: c = "horizontal",
  resizePreviewMode: d = "panel",
  resizeTargetMinimumSize: p = {
    coarse: 20,
    fine: 10
  },
  style: g,
  ...m
}) {
  const f = H({
    onLayoutChange: {},
    onLayoutChanged: {}
  }), y = we((P) => {
    le(f.current.onLayoutChange, P) || (f.current.onLayoutChange = P, u?.(P));
  }), S = we(
    (P, A, V) => {
      le(f.current.onLayoutChanged, P) || (f.current.onLayoutChanged = P, l?.(P, { isUserInteraction: A, requestedLayout: V }));
    }
  ), v = Ae(a), [z, x] = ve(), R = No({
    groupId: v,
    resizePreviewMode: d
  }), k = H(null), [O, b] = En(), w = H([]), h = H({
    lastExpandedPanelSizes: {},
    layouts: {},
    panels: [],
    separators: []
  }), I = Ye(k, i), L = Go(v, s), M = pe(
    () => ({
      getLayout: () => L.current.getLayout(),
      id: v,
      orientation: c,
      setLayout: (P) => L.current.setLayout(P)
    }),
    [v, L, c]
  ), T = we(
    (P, A) => {
      const V = ue(P);
      if (V)
        return {
          flexGrow: V.layout[A] ?? 1
        };
      if (n?.[A])
        return {
          flexGrow: n?.[A]
        };
    }
  ), E = dt({
    defaultLayout: n,
    disableCursor: o,
    resizeTargetMinimumSize: p
  }), G = pe(
    () => ({
      get disableCursor() {
        return !!E.disableCursor;
      },
      getPanelStyles: T,
      id: v,
      orientation: c,
      registerPanel: (P) => {
        const A = h.current;
        return A.panels = _e(c, [
          ...A.panels,
          P
        ]), b(), () => {
          A.panels = A.panels.filter(
            (V) => V !== P
          ), b();
        };
      },
      registerOverlay: (P) => (x(P), () => {
        x(void 0);
      }),
      registerSeparator: (P) => {
        const A = h.current;
        return A.separators = _e(c, [
          ...A.separators,
          P
        ]), b(), () => {
          A.separators = A.separators.filter(
            (V) => V !== P
          ), b();
        };
      },
      updatePanelProps: (P, { disabled: A }) => {
        const j = h.current.panels.find(
          (xe) => xe.id === P
        );
        if (!j || j.constraintProps.disabled === A)
          return;
        j.constraintProps.disabled = A;
        const U = Te(v), de = ue(v);
        U && de && ge(U, {
          ...de,
          derivedItemConstraints: Lt(U)
        });
      },
      updateSeparatorProps: (P, {
        disabled: A,
        disableDoubleClick: V
      }) => {
        const U = h.current.separators.find(
          (de) => de.id === P
        );
        U && (U.disabled = A, U.disableDoubleClick = V);
      }
    }),
    [T, v, b, c, E]
  ), N = H(null);
  return re(() => {
    const P = k.current;
    if (P === null)
      return;
    const A = h.current;
    A.panels = _e(
      c,
      A.panels
    ), A.separators = _e(
      c,
      A.separators
    ), w.current = Oo([
      ...A.panels.map(({ element: ee }) => ee),
      ...A.separators.map(({ element: ee }) => ee)
    ]);
    let V;
    if (E.defaultLayout !== void 0 && Object.keys(E.defaultLayout).length === A.panels.length) {
      V = {};
      for (const ee of A.panels) {
        const C = E.defaultLayout[ee.id];
        C !== void 0 && (V[ee.id] = C);
      }
    }
    const j = {
      disabled: !!r,
      element: P,
      id: v,
      mutableState: {
        defaultLayout: V,
        disableCursor: !!E.disableCursor,
        expandedItemSizes: h.current.lastExpandedPanelSizes,
        layouts: h.current.layouts
      },
      orientation: c,
      items: A.panels,
      resizePreviewMode: d,
      get resizeTargetMinimumSize() {
        return E.resizeTargetMinimumSize;
      },
      separators: A.separators
    };
    N.current = j;
    const U = Cn(j), {
      defaultLayoutDeferred: de,
      derivedItemConstraints: xe,
      layout: Le,
      requestedLayout: Ie
    } = ue(j.id, !0);
    if (!de && xe.length > 0)
      try {
        try {
          y(Le);
        } finally {
          S(Le, !1, Ie);
        }
      } catch (ee) {
        throw N.current = null, U(), ee;
      }
    const De = qe(v, (ee) => {
      const {
        defaultLayoutDeferred: C,
        derivedItemConstraints: $,
        layout: B,
        requestedLayout: _
      } = ee.next;
      if (C || $.length === 0)
        return;
      Rn({
        derivedItemConstraints: $,
        axis: j,
        layout: B,
        prevLayout: ee.prev?.layout,
        requestedLayout: _
      });
      const q = ae(), Z = q.state !== "active" || !q.hitRegions.some((K) => K.axis === j);
      try {
        y(B);
      } finally {
        Z && S(
          B,
          ee.isUserInteraction,
          _
        );
      }
    });
    return () => {
      N.current = null, U(), De();
    };
  }, [
    r,
    v,
    S,
    y,
    c,
    O,
    d,
    E
  ]), re(() => {
    To(w.current) || b();
  }), je(() => {
    const P = N.current;
    P && (P.mutableState.defaultLayout = n, P.mutableState.disableCursor = !!o);
  }), /* @__PURE__ */ X($n.Provider, { value: M, children: /* @__PURE__ */ X(kt.Provider, { value: G, children: /* @__PURE__ */ Gn(
    "div",
    {
      ...m,
      className: t,
      "data-group": !0,
      "data-testid": Xe(m, a),
      id: v,
      ref: I,
      style: {
        height: "100%",
        width: "100%",
        overflow: "hidden",
        position: d === "separator" ? "relative" : void 0,
        ...g,
        display: "flex",
        flexDirection: c === "horizontal" ? "row" : "column",
        flexWrap: "nowrap",
        // Inform the browser that the library is handling touch events for this element
        // but still allow users to scroll content within panels in the non-resizing direction
        // NOTE This is not an inherited style
        // See github.com/bvaughn/react-resizable-panels/issues/662
        touchAction: c === "horizontal" ? "pan-y" : "pan-x"
      },
      children: [
        e,
        R.map((P) => /* @__PURE__ */ X(
          Do,
          {
            overlay: z,
            preview: P
          },
          P.key
        ))
      ]
    }
  ) }) });
}
Fo.displayName = "Group";
function rt(e, t) {
  return `react-resizable-panels:${[e, ...t].join(":")}`;
}
function _o({
  id: e,
  panelIds: t,
  storage: n
}) {
  const o = rt(e, []), r = n.getItem(o);
  if (r)
    try {
      const i = JSON.parse(r);
      if (t) {
        const s = t.join(","), a = i[s];
        if (a && Array.isArray(a.layout) && t.length === a.layout.length) {
          const u = {};
          for (let l = 0; l < t.length; l++)
            u[t[l]] = a.layout[l];
          return u;
        }
      } else {
        const s = Object.keys(i);
        if (s.length === 1) {
          const a = i[s[0]];
          if (a && Array.isArray(a.layout)) {
            const u = s[0].split(",");
            if (u.length === a.layout.length) {
              const l = {};
              for (let c = 0; c < u.length; c++)
                l[u[c]] = a.layout[c];
              return l;
            }
          }
        }
      }
    } catch {
    }
}
function er({
  debounceSaveMs: e = 100,
  onlySaveAfterUserInteractions: t,
  panelIds: n,
  storage: o = localStorage,
  ...r
}) {
  const i = n !== void 0, s = "id" in r ? r.id : r.groupId, a = rt(s, n ?? []), u = at(
    Bo,
    () => o.getItem(a),
    () => o.getItem(a)
  ), l = pe(() => {
    if (u) {
      const y = JSON.parse(u), S = Object.values(y);
      if (Array.from(S).every((v) => typeof v == "number"))
        return y;
    }
  }, [u]), c = pe(() => {
    if (!l)
      return _o({
        id: s,
        panelIds: n,
        storage: o
      });
  }, [l, s, n, o]), d = l ?? c, p = H(null), g = se(() => {
    const y = p.current;
    y && (p.current = null, clearTimeout(y));
  }, []);
  cn(() => () => {
    g();
  }, [g]);
  const m = se(
    // The hook persists every layout commit -- including library-driven ones --
    // because it owns its own storage and the goal is to remember whatever
    // layout the user is currently looking at. Consumers that only want to
    // persist on user interaction should branch on `isUserInteraction` in
    // their own callback (see #716) rather than via this hook.
    (y, S) => {
      if (t && !S.isUserInteraction)
        return;
      g();
      const v = S.requestedLayout ?? y;
      let z;
      i ? z = rt(s, Object.keys(v)) : z = rt(s, []);
      try {
        o.setItem(z, JSON.stringify(v));
      } catch (x) {
        console.error(x);
      }
    },
    [
      g,
      i,
      s,
      t,
      o
    ]
  ), f = se(
    (y) => {
      g(), e === 0 ? m(y, { isUserInteraction: !1 }) : p.current = setTimeout(() => {
        m(y, { isUserInteraction: !1 });
      }, e);
    },
    [g, e, m]
  );
  return {
    /**
     * Pass this value to `Group` as the `defaultLayout` prop.
     */
    defaultLayout: d,
    /**
     * Attach this callback on the `Group` as the `onLayoutChange` prop.
     *
     * @deprecated Use the {@link onLayoutChanged} prop instead.
     */
    onLayoutChange: f,
    /**
     * Attach this callback on the `Group` as the `onLayoutChanged` prop.
     */
    onLayoutChanged: m
  };
}
function Bo() {
  return function() {
  };
}
function tr() {
  return ve(null);
}
function nr() {
  return H(null);
}
function or() {
  return We($n);
}
const Dn = Ue(null);
function Ho(e, t) {
  const { id: n } = ft(), o = H({
    collapse: pt,
    expand: pt,
    getSize: () => ({
      asPercentage: 0,
      inPixels: 0
    }),
    isCollapsed: () => !1,
    resize: pt
  });
  return St(t, () => o.current, []), re(() => {
    Object.assign(
      o.current,
      Mt({ axisId: n, itemId: e })
    );
  }), o;
}
function qo({
  children: e,
  className: t,
  collapsedSize: n = "0%",
  collapsedThreshold: o,
  collapsible: r = !1,
  defaultSize: i,
  disabled: s,
  elementRef: a,
  groupResizeBehavior: u = "preserve-relative-size",
  id: l,
  maxSize: c = "100%",
  minSize: d = "0%",
  onResize: p,
  panelRef: g,
  style: m,
  ...f
}) {
  const y = it(l), S = Ae(l), v = dt({
    disabled: s
  }), z = H(null), x = Ye(z, a), {
    getPanelStyles: R,
    id: k,
    orientation: O,
    registerPanel: b,
    updatePanelProps: w
  } = ft(), h = p !== null, I = we(
    (N, P, A) => {
      p?.(N, l, A);
    }
  );
  re(() => {
    const N = z.current;
    if (N !== null) {
      const P = {
        element: N,
        id: S,
        idIsStable: y,
        mutableValues: {
          expandToSize: void 0,
          prevSize: void 0
        },
        onResize: h ? I : void 0,
        constraintProps: {
          groupResizeBehavior: u,
          collapsedSize: n,
          collapsedThreshold: o,
          collapsible: r,
          defaultSize: i,
          disabled: v.disabled,
          maxSize: c,
          minSize: d
        }
      };
      return b(P);
    }
  }, [
    u,
    n,
    o,
    r,
    i,
    h,
    S,
    y,
    c,
    d,
    I,
    b,
    v
  ]), je(() => {
    w(S, { disabled: s });
  }, [s, S, w]);
  const L = Ho(S, g), M = pe(
    () => ({
      collapse: () => L.current.collapse(),
      expand: () => L.current.expand(),
      getSize: () => L.current.getSize(),
      groupId: k,
      id: S,
      isCollapsed: () => L.current.isCollapsed(),
      resize: (N) => L.current.resize(N)
    }),
    [k, S, L]
  ), T = () => {
    const N = R(k, S);
    if (N)
      return JSON.stringify(N);
  }, E = at(
    (N) => qe(k, N),
    T,
    T
  );
  let G;
  return E ? G = JSON.parse(E) : i !== void 0 ? G = {
    flexGrow: void 0,
    flexShrink: void 0,
    flexBasis: i
  } : G = { flexGrow: 1 }, /* @__PURE__ */ X(
    "div",
    {
      ...f,
      "data-disabled": s || void 0,
      "data-panel": !0,
      "data-testid": Xe(f, l),
      id: S,
      ref: x,
      style: {
        ...Vo,
        display: "flex",
        flexBasis: 0,
        flexShrink: 1,
        overflow: "visible",
        ...G
      },
      children: /* @__PURE__ */ X(
        "div",
        {
          className: t,
          style: {
            maxHeight: "100%",
            maxWidth: "100%",
            flexGrow: 1,
            overflow: "auto",
            ...m,
            // Inform the browser that the library is handling touch events for this element
            // but still allow users to scroll content within panels in the non-resizing direction
            // NOTE This is not an inherited style
            // See github.com/bvaughn/react-resizable-panels/issues/662
            touchAction: O === "horizontal" ? "pan-y" : "pan-x"
          },
          children: /* @__PURE__ */ X(Dn.Provider, { value: M, children: e })
        }
      )
    }
  );
}
qo.displayName = "Panel";
const Vo = {
  minHeight: 0,
  maxHeight: "100%",
  height: "auto",
  minWidth: 0,
  maxWidth: "100%",
  width: "auto",
  border: "none",
  borderWidth: 0,
  padding: 0,
  margin: 0
};
function rr() {
  return ve(null);
}
function ir() {
  return H(null);
}
function sr() {
  return We(Dn);
}
const jo = { flexBasis: "auto" }, Uo = { flexGrow: 0, flexShrink: 0 };
function Wo({
  children: e,
  className: t,
  disabled: n,
  disableDoubleClick: o,
  elementRef: r,
  id: i,
  preview: s,
  style: a,
  ...u
}) {
  const l = Ae(i), {
    disableCursor: c,
    id: d,
    orientation: p,
    registerSeparator: g,
    updateSeparatorProps: m
  } = ft();
  return /* @__PURE__ */ X(
    Mn,
    {
      attributes: u,
      axisId: d,
      axisOrientation: p,
      className: t,
      "data-testid": Xe(u, i),
      disableCursor: c,
      disabled: n,
      disableDoubleClick: o,
      elementRef: r,
      id: l,
      preview: s,
      registerSeparator: g,
      requiredStyle: Uo,
      style: a,
      styleDefaults: jo,
      updateSeparatorProps: m,
      children: e
    }
  );
}
Wo.displayName = "Separator";
export {
  qn as Cell,
  Eo as Grid,
  kn as Gridline,
  Fo as Group,
  qo as Panel,
  Wo as Separator,
  An as SeparatorOverlay,
  Vn as isCoarsePointer,
  Ko as useDefaultGridLayout,
  er as useDefaultLayout,
  Jo as useGridCallbackRef,
  Zo as useGridRef,
  tr as useGroupCallbackRef,
  nr as useGroupRef,
  rr as usePanelCallbackRef,
  ir as usePanelRef,
  Qo as useParentGrid,
  or as useParentGroup,
  sr as useParentPanel
};
//# sourceMappingURL=react-resizable-panels.js.map
