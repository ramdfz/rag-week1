import * as React from "react";

import { cn } from "../../lib/utils";

type TabsProps = {
  value: string;
  onValueChange: (value: string) => void;
  items: Array<{ value: string; label: string; icon?: React.ReactNode }>;
};

export function Tabs({ value, onValueChange, items }: TabsProps) {
  return (
    <div className="inline-flex rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
      {items.map((item) => (
        <button
          key={item.value}
          className={cn(
            "inline-flex h-9 items-center gap-2 rounded-lg px-3 text-sm font-semibold text-slate-600 transition",
            value === item.value && "bg-[#182127] text-white"
          )}
          onClick={() => onValueChange(item.value)}
          type="button"
        >
          {item.icon}
          {item.label}
        </button>
      ))}
    </div>
  );
}
