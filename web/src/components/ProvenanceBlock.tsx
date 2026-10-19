import type { ReactNode } from 'react';
import type { Field } from '../types';

const PROVENANCE_KEYS = ['source', 'section', 'effective_date', 'retrieved_at'] as const;
const PROVENANCE_TAIL_KEYS = ['parser_version'] as const;

function linkTarget(url: string): string | null {
  return /^https?:\/\//i.test(url) ? url : null;
}

function renderSourceUrl(url: string | null): ReactNode {
  if (url === null) return '—';
  const href = linkTarget(url);
  if (href === null) return url;
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {url}
    </a>
  );
}

function valueClass(key: string): string {
  return key === 'source' ? 'provenance-value t2' : 'provenance-value t2 mono';
}

export interface ProvenanceBlockProps {
  field: Field | null;
}

export function ProvenanceBlock({ field }: ProvenanceBlockProps) {
  return (
    <section className="provenance" aria-label="Provenance">
      <h3 className="provenance-title t1">PROVENANCE</h3>
      {field === null ? (
        <p className="provenance-empty t2 muted">No field selected.</p>
      ) : (
        <>
          <dl className="provenance-grid">
            {PROVENANCE_KEYS.map((key) => (
              <div className="provenance-row" key={key}>
                <dt className="provenance-key t1">{key}</dt>
                <dd className={valueClass(key)}>{field[key]}</dd>
              </div>
            ))}
            <div className="provenance-row">
              <dt className="provenance-key t1">source_url</dt>
              <dd className={valueClass('source_url')}>
                {renderSourceUrl(field.source_url)}
              </dd>
            </div>
            {PROVENANCE_TAIL_KEYS.map((key) => (
              <div className="provenance-row" key={key}>
                <dt className="provenance-key t1">{key}</dt>
                <dd className={valueClass(key)}>{field[key]}</dd>
              </div>
            ))}
          </dl>
          <div className="provenance-raw-header t1">raw_text</div>
          <div className="raw-well t2 mono">{field.raw_text}</div>
        </>
      )}
    </section>
  );
}
