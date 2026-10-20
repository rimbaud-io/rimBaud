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

import { useEffect, useMemo, useRef, useState } from "react";
import { CATEGORIES, TOOLS } from "./lib/tools.js";

export default function ContextMenu({
  menu,
  existingIds,
  onAdd,
  onRemove,
  onDeleteEdge,
  onAddWallet,
  onConfigureWallet,
  onDeleteWallet,
  onClose,
}) {
  const [q, setQ] = useState("");
  const [cat, setCat] = useState(null);
  const inputRef = useRef(null);

  useEffect(() => {
    setQ("");
    setCat(null);
    const t = setTimeout(() => inputRef.current?.focus(), 20);
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(t);
      window.removeEventListener("keydown", onKey);
    };
  }, [menu, onClose]);

  const items = useMemo(() => {
    const s = q.trim().toLowerCase();
    return TOOLS.filter((t) => {
      if (cat && t.category !== cat) return false;
      if (!s) return true;
      return t.label.toLowerCase().includes(s) || t.hint.toLowerCase().includes(s);
    });
  }, [q, cat]);

  if (!menu) return null;

  const compact = menu.kind === "wallets-header" || (menu.kind === "node" && menu.node?.type === "wallet");
  const style = { left: menu.x, top: menu.y };

  return (
    <>
      <div className="ctx-backdrop" onClick={onClose} onContextMenu={(e) => { e.preventDefault(); onClose(); }} />
      <div className="ctx-menu" style={style} onClick={(e) => e.stopPropagation()}>
        {menu.kind === "wallets-header" && (
          <>
            <div className="ctx-title">Wallets</div>
            <button type="button" className="ctx-item" onClick={() => onAddWallet(menu.flow)}>
              Add wallet
            </button>
          </>
        )}
        {menu.kind === "node" && menu.node?.type === "wallet" && (
          <>
            <div className="ctx-title">{menu.node?.data?.name || "wallet"}</div>
            <button type="button" className="ctx-item" onClick={() => onConfigureWallet(menu.node)}>
              Configure wallet
            </button>
            <button type="button" className="ctx-item danger" onClick={() => onDeleteWallet(menu.node)}>
              Delete wallet
            </button>
          </>
        )}
        {menu.kind === "node" && menu.node?.type !== "wallet" && (
          <>
            <div className="ctx-title">{menu.node?.data?.name || menu.node?.data?.title || menu.node?.type}</div>
            <button type="button" className="ctx-item danger" onClick={() => onRemove(menu.node.id)}>
              Remove from canvas
            </button>
            <div className="ctx-sep" />
          </>
        )}
        {menu.kind === "edge" && (
          <>
            <div className="ctx-title">wire</div>
            <button type="button" className="ctx-item danger" onClick={() => onDeleteEdge(menu.edge.id)}>
              Delete wire
            </button>
            <div className="ctx-sep" />
          </>
        )}
        {!compact && (
          <>
            <div className="ctx-title">Add node</div>
            <input
              ref={inputRef}
              className="tb-search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="search tools…"
            />
            <div className="ctx-cats">
              <button type="button" className={!cat ? "on" : ""} onClick={() => setCat(null)}>
                all
              </button>
              {CATEGORIES.map((c) => (
                <button key={c.id} type="button" className={cat === c.id ? "on" : ""} onClick={() => setCat(c.id)}>
                  {c.label}
                </button>
              ))}
            </div>
            <div className="ctx-list">
              {items.map((t) => {
                const on = t.unique && existingIds.has(t.toolId);
                return (
                  <button
                    key={t.toolId}
                    type="button"
                    className={`ctx-item${on ? " dim" : ""}`}
                    onClick={() => onAdd(t, menu.flow)}
                  >
                    <i className="dot" style={{ background: t.color }} />
                    <span>{t.icon} {t.label}</span>
                    {on && <em>on</em>}
                  </button>
                );
              })}
              {!items.length && <div className="ctx-empty">no tools</div>}
            </div>
          </>
        )}
      </div>
    </>
  );
}
