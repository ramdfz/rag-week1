import * as React from "react";

import { cn } from "../../lib/utils";

export function Badge({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border border-[#FC7900]/25 bg-[#F4AD0B]/10 px-2.5 py-1 text-xs font-medium text-[#182127]",
        className
      )}
      {...props}
    />
  );
}
