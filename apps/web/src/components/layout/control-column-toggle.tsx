"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useEffect, useState } from "react";

const STORAGE_KEY = "irms:control-column-collapsed";
const COLLAPSED_CLASS = "control-column-is-collapsed";

function applyCollapsedState(collapsed: boolean) {
  document.documentElement.classList.toggle(COLLAPSED_CLASS, collapsed);
}

export function ControlColumnToggle() {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const savedState = window.localStorage.getItem(STORAGE_KEY) === "true";
    setCollapsed(savedState);
    applyCollapsedState(savedState);

    return () => document.documentElement.classList.remove(COLLAPSED_CLASS);
  }, []);

  function toggleControls() {
    const nextState = !collapsed;
    setCollapsed(nextState);
    applyCollapsedState(nextState);
    window.localStorage.setItem(STORAGE_KEY, String(nextState));
  }

  const label = collapsed ? "Show controls" : "Hide controls";

  return (
    <div className="control-column-toggle">
      <button
        type="button"
        className="control-column-toggle__button"
        onClick={toggleControls}
        aria-label={label}
        aria-pressed={collapsed}
        title={label}
      >
        {collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
      </button>
    </div>
  );
}
