"use client";

import { useTranslation } from "@/components/layout/language-provider";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  children: ReactNode;
  variant?: "default" | "outline" | "secondary";
};

export function IconButton({ label, children, variant = "outline", ...props }: IconButtonProps) {
  const tr = useTranslation();
  return (
    <Tooltip label={tr(label)}>
      <Button type="button" variant={variant} size="icon" aria-label={tr(label)} {...props}>
        {tr(children)}
      </Button>
    </Tooltip>
  );
}
