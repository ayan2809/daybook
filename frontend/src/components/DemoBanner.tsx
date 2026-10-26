import { useMe } from "../me";

export function DemoBanner() {
  const { me } = useMe();
  if (me?.role !== "demo") return null;
  return (
    <div className="demo-banner" role="note">
      Demo: two made-up work weeks for Ayan, a fictional engineer. Nothing you type is kept after 24 h.
    </div>
  );
}
