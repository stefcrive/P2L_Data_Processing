import type { ButtonHTMLAttributes, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  children: ReactNode;
  variant?: "default" | "outline" | "secondary";
};

export function IconButton({ label, children, variant = "outline", ...props }: IconButtonProps) {
  return (
    <Tooltip label={label}>
      <Button type="button" variant={variant} size="icon" aria-label={label} {...props}>
        {children}
      </Button>
    </Tooltip>
  );
}
