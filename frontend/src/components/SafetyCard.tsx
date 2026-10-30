import { Phone } from "lucide-react";
import type { SafetyInfo } from "../types";

export function telHref(tel: string): string {
  return tel.startsWith("tel:") ? tel : `tel:${tel.replace(/[^\d+]/g, "")}`;
}

export function SafetyCard({ info, onPause, pausing }: { info: SafetyInfo; onPause: () => void; pausing?: boolean }) {
  return (
    <section className="card safety-card" role="alert" aria-labelledby="safety-title">
      <h2 id="safety-title" className="visually-hidden">
        Support
      </h2>
      <p>{info.message}</p>
      <div className="row wrap gap">
        <a className="btn btn-primary" href={telHref(info.tel)}>
          <Phone size={18} aria-hidden="true" /> Call {info.helpline_name} {info.helpline_number}
        </a>
        <button className="btn" onClick={onPause} disabled={pausing}>
          Pause reflection
        </button>
      </div>
    </section>
  );
}
