"use client";

import { useEffect, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

type DecimalInputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "type" | "value" | "onChange" | "inputMode"> & {
  value: number | null | undefined;
  onValueChange: (value: number) => void;
};

function parseDecimalInput(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return 0;
  }
  const normalized = trimmed.replace(",", ".");
  if (!/^[-+]?(\d+(\.\d*)?|\.\d+)$/.test(normalized)) {
    return null;
  }
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatDecimalInput(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "0";
}

export function DecimalInput({ value, onValueChange, onBlur, onFocus, onKeyDown, ...props }: DecimalInputProps) {
  const [draft, setDraft] = useState(() => formatDecimalInput(value));
  const [isEditing, setIsEditing] = useState(false);

  useEffect(() => {
    if (!isEditing) {
      setDraft(formatDecimalInput(value));
    }
  }, [isEditing, value]);

  function resetDraft() {
    setDraft(formatDecimalInput(value));
    setIsEditing(false);
  }

  function commitDraft() {
    const parsed = parseDecimalInput(draft);
    if (parsed == null) {
      resetDraft();
      return;
    }
    onValueChange(parsed);
    setDraft(formatDecimalInput(parsed));
    setIsEditing(false);
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      commitDraft();
      event.currentTarget.blur();
      onKeyDown?.(event);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      resetDraft();
      event.currentTarget.blur();
      onKeyDown?.(event);
      return;
    }
    onKeyDown?.(event);
  }

  return (
    <input
      {...props}
      type="text"
      inputMode="decimal"
      value={draft}
      onFocus={(event) => {
        setIsEditing(true);
        onFocus?.(event);
      }}
      onChange={(event) => {
        const rawValue = event.target.value;
        setDraft(rawValue);
        const parsed = parseDecimalInput(rawValue);
        if (parsed != null) {
          onValueChange(parsed);
        }
      }}
      onBlur={(event) => {
        commitDraft();
        onBlur?.(event);
      }}
      onKeyDown={handleKeyDown}
    />
  );
}
