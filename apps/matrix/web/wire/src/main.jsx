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

import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./index.css";

class WireCrash extends React.Component {
  constructor(props) {
    super(props);
    this.state = { err: null };
  }
  static getDerivedStateFromError(err) {
    return { err };
  }
  componentDidCatch(err, info) {
    console.error("wire crash", err, info);
  }
  render() {
    if (this.state.err) {
      return (
        <div style={{ padding: 24, color: "#ff4d6d", fontFamily: "IBM Plex Mono, monospace" }}>
          <p>Wire crashed: {String(this.state.err.message || this.state.err)}</p>
          <button
            type="button"
            style={{ marginTop: 12, padding: "6px 12px", cursor: "pointer" }}
            onClick={() => window.location.reload()}
          >
            Reload Wire
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <WireCrash>
      <App />
    </WireCrash>
  </React.StrictMode>
);
