import type { View } from '../App';
import type { Publication } from '../types';

const VIEWS: { id: View; label: string }[] = [
  { id: 'map', label: 'Map' },
  { id: 'airport', label: 'Airport' },
  { id: 'register', label: 'Register' }
];

export interface TopBarProps {
  view: View;
  onViewChange: (view: View) => void;
  publication: Publication | null;
}

export function TopBar({ view, onViewChange, publication }: TopBarProps) {
  return (
    <header className="topbar">
      <div className="topbar-brand t1">eAIP BELGIUM</div>

      <nav className="topbar-tabs" role="tablist" aria-label="Views">
        {VIEWS.map((item) => (
          <button
            type="button"
            key={item.id}
            role="tab"
            aria-selected={view === item.id}
            className="topbar-tab t3"
            onClick={() => onViewChange(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <div className="topbar-status">
        <span className="cycle-pill t1 mono">
          {publication === null
            ? '—'
            : `${publication.cycleId} · ${publication.amendmentId}`}
        </span>
      </div>
    </header>
  );
}
