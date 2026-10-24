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

import { useEffect, useState } from "react";

export default function WalletModal({ form, busy, error, onClose, onSave }) {
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [useVault, setUseVault] = useState(true);
  const [occupied, setOccupied] = useState([]);

  useEffect(() => {
    if (!form) return;
    setName(form.name || (form.mode === "add" ? "Subaccount" : ""));
    setAddress(form.address || "");
    setUseVault(form.use_vault !== false);
    fetch("/api/wire/wallets")
      .then((r) => r.json())
      .then((d) => setOccupied(d.occupied || []))
      .catch(() => {});
  }, [form]);

  if (!form) return null;
  const adding = form.mode === "add";
  const title = adding ? "Add subaccount" : "Configure wallet";
  const self = String((form.node && form.node.id) || "");
  const taken = occupied.find(
    (o) =>
      o.address &&
      address &&
      o.address.toLowerCase() === address.toLowerCase() &&
      o.id !== self
  );

  return (
    <>
      <div className="ctx-backdrop" onClick={onClose} />
      <div className="wallet-modal" role="dialog" aria-label={title}>
        <div className="compile-head">
          <strong>{title}</strong>
          <span>{adding ? "new Hyperliquid sub" : "wire subaccount"}</span>
          <button type="button" onClick={onClose}>close</button>
        </div>
        <label>
          Name
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Subaccount" />
        </label>
        <label>
          Subaccount address
          <input
            type="text"
            value={address}
            onChange={(e) => setAddress(e.target.value.trim())}
            placeholder="0x from Hyperliquid"
            spellCheck={false}
          />
        </label>
        {taken ? <p className="wallet-err">Already used by {taken.name}</p> : null}
        <label className="chk">
          <input type="checkbox" checked={useVault} onChange={(e) => setUseVault(e.target.checked)} />
          Hyperliquid vault subaccount
        </label>
        <p className="health">
          Create the subaccount in Hyperliquid first, then paste the address here.
          Once it is in the list it can wire to any controller. This does not move funds and does not restart a hunter.
          Connector keys stay on Confiig.
        </p>
        {error ? <p className="wallet-err">{error}</p> : null}
        <div className="wallet-actions">
          <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
          <button
            type="button"
            className="go"
            disabled={busy || !name.trim() || !address || !!taken}
            onClick={() => onSave({
              ...form,
              id: self || undefined,
              name: name.trim(),
              address: address.trim(),
              use_vault: useVault,
              custom: true,
            })}
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </>
  );
}
