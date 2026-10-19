import type { View } from '../App';
import type { Publication } from '../types';

const VIEWS: { id: View; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'map', label: 'Map' },
  { id: 'register', label: 'Register' }
];

export interface TopBarProps {
  view: View;
  onViewChange: (view: View) => void;
  publication: Publication | null;
  registerCount: number;
}

export function TopBar({ view, onViewChange, publication, registerCount }: TopBarProps) {
  const cycle =
    publication === null ? '—' : `${publication.cycleId} · ${publication.amendmentId}`;
  const effective =
    publication === null ? 'eff —' : `eff ${publication.effectiveDate}`;

  return (
    <header className="topbar">
      <div className="topbar-left">
        <span className="topbar-brand">eAIP BELGIUM</span>
        <span className="topbar-divider" aria-hidden="true" />
        <span className="topbar-cycle t2 mono">{cycle}</span>
        <span className="topbar-eff t2 mono">{effective}</span>
      </div>

      <nav className="topbar-tabs" role="tablist" aria-label="Views">
        {VIEWS.map((item) => (
          <button
            type="button"
            key={item.id}
            role="tab"
            aria-selected={view === item.id}
            className="topbar-tab"
            onClick={() => onViewChange(item.id)}
          >
            {item.label}
            {item.id === 'register' ? (
              <span className="tab-badge t1 mono">{registerCount}</span>
            ) : null}
          </button>
        ))}
      </nav>
    </header>
  );
}
