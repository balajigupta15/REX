import { FlaskConical } from "lucide-react";
import { Badge } from "./ui/badge";
export const SampleBadge = ({ label }: { label?: string }) => (
  <Badge tone="warn" title={label || "Synthetic trader behaviour on real prices. Not a real trader's record."}>
    <FlaskConical className="h-3 w-3" /> SAMPLE DATA
  </Badge>
);
