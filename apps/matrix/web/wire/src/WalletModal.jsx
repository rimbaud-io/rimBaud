// Copyright 2026 Laurence Sterne www.rimbaud.io
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

import { useEffect, useState } from "react";

export default function WalletModal({ form, busy, error, onClose, onSave }) {
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [useVault, setUseVault] = useState(true);
  const [restart, setRestart] = useState(false);
  const [bindId, setBindId] = useState("");
  const [slots, setSlots] = useState([]);
  const [occupied, setOccupied] = useState([]);

  useEffect(() => {
    if (!form) return;
    setName(form.name || (form.mode === "add" ? "Subaccount" : ""));
    setAddress(form.address || "");
    setUseVault(form.use_vault !== false);
    setRestart(false);
    setBindId(form.bind_id || form.botId || "");
    if (form.mode === "add" || form.custom) {
      fetch("/api/wire/wallets")
        .then((r) => r.json())
        .then((d) => {
          setSlots(d.hunters || []);
          setOccupied(d.occupied || []);
        })
        .catch(() => {});
    }
  }, [form]);

  if (!form) return null;
  const hunter = !!(form.botId && !form.custom);
  const adding = form.mode === "add";
  const title = adding ? "Add subaccount" : "Configure wallet";
  const taken = occupied.find(
    (o) =>
      o.address &&
      address &&
      o.address.toLowerCase() === address.toLowerCase() &&
      o.name !== name
  );
  const emptySlots = slots.filter((s) => s.empty);

  return (
    <>
      <div className="ctx-backdrop" onClick={onClose} />
      <div className="wallet-modal" role="dialog" aria-label={title}>
        <div className="compile-head">
          <strong>{title}</strong>
          <span>{hunter ? form.botId : adding ? "new Hyperliquid sub" : "extra canvas wallet"}</span>
          <button type="button" onClick={onClose}>close</button>
        </div>
        <label>
          Name
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="DMan Maker sub" />
        </label>
        <label>
          Subaccount address
          <input
            type="text"
            value={address}
            onChange={(e) => setAddress(e.target.value.trim())}
            placeholder="0x… from Hyperliquid"
            spellCheck={false}
          />
        </label>
        {taken ? <p className="wallet-err">Already used by {taken.name}</p> : null}
        {(adding || hunter) && (
          <label className="chk">
            <input type="checkbox" checked={useVault} onChange={(e) => setUseVault(e.target.checked)} />
            Use as Hyperliquid subaccount (vault)
          </label>
        )}
        {adding && (
          <label>
            Attach to hunter
            <select value={bindId} onChange={(e) => setBindId(e.target.value)}>
              <option value="">Canvas only (income map)</option>
              {emptySlots.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} — needs unique sub
                </option>
              ))}
              {slots.filter((s) => !s.empty).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} — replace current
                </option>
              ))}
            </select>
          </label>
        )}
        {(hunter || bindId) && (
          <label className="chk">
            <input type="checkbox" checked={restart} onChange={(e) => setRestart(e.target.checked)} />
            Restart container after save
          </label>
        )}
        <p className="health">
          Create the subaccount in Hyperliquid first, then paste the 0x here.
          It must be unique (not Copy / Swing / Helliot / Surfn / Shorty / Dman / Only Up).
          Does not close positions. Restart only if you tick it.
        </p>
        {error ? <p className="wallet-err">{error}</p> : null}
        <div className="wallet-actions">
          <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
          <button
            type="button"
            className="go"
            disabled={busy || !name.trim() || ((adding || hunter) && !address)}
            onClick={() => onSave({
              ...form,
              name: name.trim(),
              address: address.trim(),
              use_vault: useVault,
              restart,
              bind_id: adding ? bindId : (form.botId || ""),
            })}
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </>
  );
}
