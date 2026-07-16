import * as React from "react";

import { cn } from "../../lib/utils";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      className={cn(
        "min-h-24 w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm text-[#182127] outline-none transition focus:border-[#FC7900] focus:ring-2 focus:ring-[#FC7900]/20 disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      ref={ref}
      {...props}
    />
  )
);
Textarea.displayName = "Textarea";
