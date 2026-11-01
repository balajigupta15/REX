"use client";
import * as React from "react";
import { cn } from "@/lib/utils";
const base = "h-9 w-full rounded-md border border-line bg-ink px-3 text-sm text-text placeholder:text-mute focus:outline-none focus:border-mute/60 disabled:opacity-50";
const FieldId = React.createContext<string | undefined>(undefined);
export function FormField({ children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const id = React.useId();
  return <FieldId.Provider value={id}><div {...props}>{children}</div></FieldId.Provider>;
}
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, id, ...p }, ref) => {
  const fieldId = React.useContext(FieldId);
  return <input ref={ref} id={id ?? fieldId} className={cn(base, className)} {...p} />;
});
Input.displayName = "Input";
export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, id, ...p }, ref) => {
  const fieldId = React.useContext(FieldId);
  return <textarea ref={ref} id={id ?? fieldId} className={cn(base.replace("h-9 ", "min-h-[96px] py-2 "), "resize-y leading-relaxed", className)} {...p} />;
});
Textarea.displayName = "Textarea";
export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(({ className, id, ...p }, ref) => {
  const fieldId = React.useContext(FieldId);
  return <select ref={ref} id={id ?? fieldId} className={cn(base, "pr-8", className)} {...p} />;
});
Select.displayName = "Select";
export function Label({ className, htmlFor, ...p }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  const id = React.useContext(FieldId);
  return <label htmlFor={htmlFor ?? id} className={cn("mb-1.5 block text-xs font-medium tracking-normal text-mute", className)} {...p} />;
}
