"use client";
import * as React from "react";
import * as S from "@radix-ui/react-switch";
import { cn } from "@/lib/utils";
export const Switch = ({ className, ...p }: React.ComponentProps<typeof S.Root>) => (
  <S.Root className={cn("relative h-5 w-9 shrink-0 rounded-full bg-line transition data-[state=checked]:bg-rex", className)} {...p}>
    <S.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-white transition data-[state=checked]:translate-x-[18px]" />
  </S.Root>
);
