// Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import { useCallback, useEffect, useRef, useState } from "react";
import { boardCategories, TOOLS, refreshTools } from "./lib/tools.js";

const LS = "matrix-wire-toolbox-v1";

function loadPrefs() {
  const blank = {
    x: null,
    y: null,
    collapsed: false,
    hiddenCats: [],
    hiddenItems: [],
    closedCats: [],
    hideInspect: false,
  };
  try {
    const raw = JSON.parse(localStorage.getItem(LS) || "{}") || {};
    const prefs = { ...blank, ...raw };
    if (!raw.v2FoldSet) {
      const closed = new Set(prefs.closedCats || []);
      closed.add("hummingbots_v2");
      prefs.closedCats = [...closed];
      prefs.v2FoldSet = true;
      try {
        localStorage.setItem(LS, JSON.stringify(prefs));
      } catch {
        /* ignore */
      }
    }
    return prefs;
  } catch {
    return { ...blank, closedCats: ["hummingbots_v2"], v2FoldSet: true };
  }
}

export default function Toolbox({
  query,
  onQuery,
  existingIds,
  onAdd,
  onPick,
  toolPickId,
  selected,
  inspect,
  onRemoveSelected,
  onWalletsHeaderMenu,
  board = "desk",
}) {
  const [prefs, setPrefs] = useState(loadPrefs);
  const [tools, setTools] = useState(TOOLS);
  const drag = useRef(null);
  const box = useRef(null);

  const loadCatalog = useCallback(() => {
    fetch("/api/wire/catalog")
      .then((r) => r.json())
      .then((d) => {
        if (!d || d.ok === false) return;
        setTools(refreshTools({
          hunters: d.hunters || [],
          extraWallets: d.wallets || [],
          extraLeaders: d.traders || [],
          v2Sleepers: d.v2_sleepers || [],
          board,
        }));
      })
      .catch(() => {});
  }, [board]);

  useEffect(() => {
    loadCatalog();
    window.addEventListener("wire-catalog", loadCatalog);
    return () => window.removeEventListener("wire-catalog", loadCatalog);
  }, [loadCatalog]);

  const setP = useCallback((patch) => {
    setPrefs((p) => {
      const n = { ...p, ...(typeof patch === "function" ? patch(p) : patch) };
      try {
        localStorage.setItem(LS, JSON.stringify(n));
      } catch {
        /* ignore */
      }
      return n;
    });
  }, []);

  const clampPos = useCallback((x, y) => {
    const el = box.current;
    const w = el ? el.offsetWidth : 280;
    const h = el ? el.offsetHeight : 48;
    const maxX = Math.max(8, window.innerWidth - w - 8);
    const maxY = Math.max(8, window.innerHeight - Math.min(h, 80) - 8);
    return {
      x: Math.min(Math.max(8, x), maxX),
      y: Math.min(Math.max(8, y), maxY),
    };
  }, []);

  const onPointerDown = (e) => {
    if (e.button !== 0) return;
    if (e.target.closest("button, input, a")) return;
    const el = box.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    drag.current = { dx: e.clientX - r.left, dy: e.clientY - r.top, pos: null };
    el.classList.add("dragging");
    el.setPointerCapture(e.pointerId);
    e.preventDefault();
  };

  const onPointerMove = (e) => {
    if (!drag.current) return;
    const pos = clampPos(e.clientX - drag.current.dx, e.clientY - drag.current.dy);
    drag.current.pos = pos;
    const el = box.current;
    if (!el) return;
    el.style.left = pos.x + "px";
    el.style.top = pos.y + "px";
    el.style.right = "auto";
  };

  const onPointerUp = (e) => {
    if (!drag.current) return;
    const pos = drag.current.pos;
    drag.current = null;
    box.current?.classList.remove("dragging");
    try {
      box.current?.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    if (pos) setP(pos);
  };

  useEffect(() => {
    const onResize = () => {
      setPrefs((p) => {
        if (p.x == null || p.y == null) return p;
        const n = { ...p, ...clampPos(p.x, p.y) };
        try {
          localStorage.setItem(LS, JSON.stringify(n));
        } catch {
          /* ignore */
        }
        return n;
      });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [clampPos]);

  const q = (query || "").trim().toLowerCase();
  const match = (t) =>
    !q ||
    t.label.toLowerCase().includes(q) ||
    t.hint.toLowerCase().includes(q) ||
    t.category.includes(q);

  const hiddenCats = new Set(prefs.hiddenCats || []);
  const hiddenItems = new Set(prefs.hiddenItems || []);
  const closedCats = new Set(prefs.closedCats || []);

  const toggleCatClosed = (id) => {
    setP((p) => {
      const s = new Set(p.closedCats || []);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return { closedCats: [...s] };
    });
  };

  const toggleCatHidden = (id, ev) => {
    ev.stopPropagation();
    ev.preventDefault();
    setP((p) => {
      const s = new Set(p.hiddenCats || []);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return { hiddenCats: [...s] };
    });
  };

  const toggleItemHidden = (id, ev) => {
    ev.stopPropagation();
    ev.preventDefault();
    setP((p) => {
      const s = new Set(p.hiddenItems || []);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return { hiddenItems: [...s] };
    });
  };

  const showAll = () => setP({ hiddenCats: [], hiddenItems: [], closedCats: [], hideInspect: false });

  const hiddenCatList = boardCategories(board).filter((c) => hiddenCats.has(c.id));
  const hiddenItemList = tools.filter((t) => hiddenItems.has(t.toolId));
  const nHidden = hiddenCatList.length + hiddenItemList.length + (prefs.hideInspect ? 1 : 0);

  const style = {
    left: prefs.x != null ? prefs.x : "auto",
    top: prefs.y != null ? prefs.y : 12,
    right: prefs.x == null ? 12 : "auto",
  };

  return (
    <aside
      ref={box}
      className={`toolbox${prefs.collapsed ? " fit" : ""}${drag.current ? " dragging" : ""}`}
      style={style}
    >
      <div
        className="tb-head"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        title="Drag to move toolbox"
      >
        <span className="tb-grip" aria-hidden>
          ∷
        </span>
        <div className="ins-kicker">toolbox</div>
        <h2>Tools</h2>
        <div className="tb-head-btns">
          {prefs.hideInspect ? (
            <button
              type="button"
              className="tb-icon"
              title="Show inspect"
              onClick={() => setP({ hideInspect: false })}
            >
              inspect
            </button>
          ) : null}
          <button
            type="button"
            className="tb-icon"
            title={prefs.collapsed ? "Expand toolbox" : "Fit toolbox"}
            onClick={() => setP({ collapsed: !prefs.collapsed })}
          >
            {prefs.collapsed ? "open" : "fit"}
          </button>
        </div>
      </div>

      {!prefs.collapsed && (
        <>
          <input
            className="tb-search"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="search hummingbots, gauges…"
          />
          <p className="tb-hint">
            Click a tool to read it. Drag it onto the canvas to place it.
          </p>

          <div className="tb-tools">
            {boardCategories(board).map((cat) => {
              if (hiddenCats.has(cat.id) && !q) return null;
              const items = tools.filter(
                (t) =>
                  t.category === cat.id &&
                  match(t) &&
                  (q ? true : !hiddenItems.has(t.toolId))
              );
              if (!items.length && !q) return null;
              const catHidden = hiddenCats.has(cat.id);
              const folded = closedCats.has(cat.id) && !q;
              return (
                <div key={cat.id} className={`tb-cat${catHidden ? " dim" : ""}`}>
                  <div
                    className="tb-cat-h"
                    onContextMenu={(e) => {
                      if (cat.id !== "wallets" || !onWalletsHeaderMenu) return;
                      e.preventDefault();
                      e.stopPropagation();
                      onWalletsHeaderMenu(e);
                    }}
                  >
                    <button
                      type="button"
                      className="tb-fold"
                      title={folded ? "Unhide menu" : "Hide menu"}
                      onClick={() => toggleCatClosed(cat.id)}
                    >
                      {folded ? "▸" : "▾"}
                    </button>
                    <span
                      className="tb-cat-lab"
                      title={cat.id === "wallets" ? "Right-click: add wallet" : undefined}
                      onClick={() => toggleCatClosed(cat.id)}
                    >
                      {cat.label}
                    </span>
                    <button
                      type="button"
                      className={`tb-eye${catHidden ? " off" : ""}`}
                      title={catHidden ? "Unhide this menu" : "Hide this menu"}
                      onClick={(e) => toggleCatHidden(cat.id, e)}
                    >
                      {catHidden ? "◌" : "◉"}
                    </button>
                  </div>
                  {!folded &&
                    items.map((t) => {
                      const on = t.unique && existingIds.has(t.toolId);
                      const itemHidden = hiddenItems.has(t.toolId);
                      return (
                        <div key={t.toolId} className={`tb-row${itemHidden ? " dim" : ""}`}>
                          <button
                            type="button"
                            className={`tb-item${on ? " on" : ""}${toolPickId === t.toolId ? " picked" : ""}`}
                            draggable
                            onDragStart={(e) => {
                              e.dataTransfer.setData("application/x-wire-tool", t.toolId);
                              e.dataTransfer.effectAllowed = "copy";
                            }}
                            onClick={() => (onPick ? onPick(t) : onAdd(t))}
                            title={on ? "Already on canvas — click to read" : (t.menu || t.hint)}
                          >
                            <i className="dot" style={{ background: t.color }} />
                            <span className="ico">{t.icon}</span>
                            <span className="lab">
                              {t.label}
                              {t.menu ? <span className="sub">{t.menu}</span> : null}
                            </span>
                            {on ? <span className="st">on</span> : <span className="st add">+</span>}
                          </button>
                          <button
                            type="button"
                            className={`tb-eye${itemHidden ? " off" : ""}`}
                            title={itemHidden ? "Unhide item" : "Hide item"}
                            onClick={(e) => toggleItemHidden(t.toolId, e)}
                          >
                            {itemHidden ? "◌" : "◉"}
                          </button>
                        </div>
                      );
                    })}
                </div>
              );
            })}

            {nHidden > 0 && !q && (
              <div className="tb-cat tb-hidden">
                <div className="tb-cat-h">
                  <span className="tb-cat-lab">Hidden · {nHidden}</span>
                  <button type="button" className="tb-eye off" onClick={showAll} title="Unhide all">
                    show all
                  </button>
                </div>
                {prefs.hideInspect && (
                  <button
                    type="button"
                    className="tb-ghost"
                    onClick={() => setP({ hideInspect: false })}
                  >
                    unhide inspect
                  </button>
                )}
                {hiddenCatList.map((c) => (
                  <button
                    key={"hc-" + c.id}
                    type="button"
                    className="tb-ghost"
                    onClick={(e) => toggleCatHidden(c.id, e)}
                  >
                    unhide menu {c.label}
                  </button>
                ))}
                {hiddenItemList.map((t) => (
                  <button
                    key={"hi-" + t.toolId}
                    type="button"
                    className="tb-ghost"
                    onClick={(e) => toggleItemHidden(t.toolId, e)}
                  >
                    unhide {t.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {!prefs.hideInspect && (
            <div className={`tb-inspect${toolPickId ? " sheet" : ""}`}>
              <div className="tb-cat-h tb-inspect-h">
                <span className="tb-cat-lab">{toolPickId ? "detail" : "inspect"}</span>
                <button
                  type="button"
                  className="tb-eye"
                  title="Hide inspect"
                  onClick={() => setP({ hideInspect: true })}
                >
                  hide
                </button>
              </div>
              {inspect}
              {selected && (
                <button type="button" className="tb-remove" onClick={onRemoveSelected}>
                  Remove from canvas
                </button>
              )}
            </div>
          )}
        </>
      )}
    </aside>
  );
}
