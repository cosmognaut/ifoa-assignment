import { useEffect, useState } from 'react';
import { getAirport } from '../api';
import type { AirportResponse, Field, GeoJsonFeature, Publication } from '../types';
import { renderValue } from './FieldRow';

interface AirportViewProps {
  aerodromes: GeoJsonFeature[];
  selectedKey: string | null;
  onSelect: (naturalKey: string) => void;
  onBack: () => void;
  publication: Publication | null;
}

type LoadState =
  | { phase: 'loading' }
  | { phase: 'ready'; data: AirportResponse }
  | { phase: 'error'; message: string };

const HOUR_FIELDS: { key: string; label: string }[] = [
  { key: 'hoursOperator', label: 'Operator' },
  { key: 'hoursCustoms', label: 'Customs & immigration' },
  { key: 'hoursAisBriefing', label: 'AIS briefing' },
  { key: 'hoursAro', label: 'ATS reporting (ARO)' },
  { key: 'hoursMet', label: 'MET briefing' },
  { key: 'hoursATS', label: 'ATS' },
  { key: 'hoursFuelling', label: 'Fuelling' },
  { key: 'hoursHandling', label: 'Handling' },
  { key: 'hoursSecurity', label: 'Security' },
  { key: 'hoursHealth', label: 'Health & sanitation' },
  { key: 'hoursDeicing', label: 'De-icing' }
];

const CONTACT_FIELDS: { key: string; label: string }[] = [
  { key: 'operatorName', label: 'Operator' },
  { key: 'operatorAddress', label: 'Address' },
  { key: 'operatorTel', label: 'Telephone' },
  { key: 'operatorFax', label: 'Fax' },
  { key: 'operatorEmail', label: 'Email' },
  { key: 'operatorAfs', label: 'AFS' }
];

function fieldOf(feature: GeoJsonFeature | null, key: string): Field | undefined {
  return feature === null ? undefined : feature.properties.fields[key];
}

function textOf(feature: GeoJsonFeature | null, key: string): string | null {
  const field = fieldOf(feature, key);
  if (field === undefined) return null;
  const { value } = field;
  if (typeof value === 'string' && value.trim() !== '') return value.trim();
  if (typeof value === 'number') return String(value);
  return null;
}

/** "BRUSSELS / Brussels-National" -> "Brussels-National" */
function shortName(feature: GeoJsonFeature | null): string | null {
  const full = textOf(feature, 'airportName');
  if (full === null) return null;
  const separator = full.indexOf(' / ');
  return separator === -1 ? full : full.slice(separator + 3).trim();
}

function Provenance({ field }: { field: Field | undefined }) {
  if (field === undefined) return null;
  return (
    <span className="summary-meta t1 mono">
      {field.section} · eff {field.effective_date}
    </span>
  );
}

function SummaryRow({ label, field }: { label: string; field: Field | undefined }) {
  if (field === undefined) return null;
  return (
    <div className="summary-row">
      <span className="summary-label t3">{label}</span>
      <span className="summary-value-line">
        <span className="summary-value t3 mono">{renderValue(field.value)}</span>
        {field.unit === null ? null : <span className="summary-unit t2 mono"> {field.unit}</span>}
        <Provenance field={field} />
      </span>
    </div>
  );
}

function InlineField({ label, field }: { label: string; field: Field | undefined }) {
  if (field === undefined) return null;
  return (
    <div className="summary-inline-item">
      <span className="summary-inline-label t1">{label}</span>
      <span className="summary-inline-value t3">{renderValue(field.value)}</span>
      <span className="summary-inline-meta t1 mono">
        {field.section} · eff {field.effective_date}
      </span>
    </div>
  );
}

function NumberCell({ field }: { field: Field | undefined }) {
  if (field === undefined || field.value === null) return <td className="runway-cell">—</td>;
  return (
    <td className="runway-cell mono">
      {String(field.value)}
      {field.unit === null ? null : <span className="summary-unit t2 mono"> {field.unit}</span>}
    </td>
  );
}

export function AirportView({
  aerodromes,
  selectedKey,
  onSelect,
  onBack,
  publication
}: AirportViewProps) {
  const fallback = aerodromes[0]?.properties.naturalKey ?? null;
  const icao = selectedKey !== null && aerodromes.some((f) => f.properties.naturalKey === selectedKey)
    ? selectedKey
    : fallback;

  const [state, setState] = useState<LoadState>({ phase: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (icao === null) return;
    let cancelled = false;
    setState({ phase: 'loading' });

    getAirport(icao)
      .then((data) => {
        if (!cancelled) setState({ phase: 'ready', data });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          phase: 'error',
          message: error instanceof Error ? error.message : 'Unknown error'
        });
      });

    return () => {
      cancelled = true;
    };
  }, [icao, attempt]);

  const aerodrome = state.phase === 'ready' ? state.data.aerodrome : null;

  return (
    <section className="airport" aria-label="Airport operational summary">
      <div className="airport-rail">
        <button type="button" className="airport-back t1" onClick={onBack}>
          ← Overview
        </button>
        <div className="airport-rail-head t1">AERODROMES</div>
        {aerodromes.map((feature) => {
          const key = feature.properties.naturalKey;
          const selected = key === icao;
          return (
            <button
              type="button"
              key={key}
              className={`airport-rail-row t3 mono${selected ? ' is-selected' : ''}`}
              aria-pressed={selected}
              onClick={() => onSelect(key)}
            >
              {key}
            </button>
          );
        })}
      </div>

      <div className="airport-panel">
        {icao === null ? (
          <p className="airport-placeholder t3">No aerodromes in this cycle.</p>
        ) : null}

        {state.phase === 'loading' ? (
          <p className="airport-placeholder t3">Loading {icao}…</p>
        ) : null}

        {state.phase === 'error' ? (
          <div className="airport-error">
            <p className="t3">{state.message}</p>
            <button type="button" className="retry-button t3" onClick={() => setAttempt((n) => n + 1)}>
              Retry
            </button>
          </div>
        ) : null}

        {state.phase === 'ready' && icao !== null ? (
          <>
            <header className="airport-head">
              <div className="airport-icao t5 mono">{icao}</div>
              <div className="airport-name t4">{shortName(aerodrome) ?? '—'}</div>
              <div className="airport-meta t2 mono">
                <span>elev {renderValue(fieldOf(aerodrome, 'elevation')?.value ?? null)}</span>
                <span>mag var {textOf(aerodrome, 'magneticVariation') ?? '—'}</span>
                <span>traffic {textOf(aerodrome, 'trafficPermitted') ?? '—'}</span>
                <span>eff {publication?.effectiveDate ?? '—'}</span>
              </div>
            </header>

            <div className="airport-grid">
              <section className="airport-card airport-card-wide" aria-label="Runways">
                <h3 className="airport-card-title t1">Runways</h3>
                <table className="runway-table">
                  <thead>
                    <tr>
                      <th className="t1">RWY</th>
                      <th className="t1">True brg</th>
                      <th className="t1">Dimensions</th>
                      <th className="t1">Surface</th>
                      <th className="t1">TORA</th>
                      <th className="t1">TODA</th>
                      <th className="t1">ASDA</th>
                      <th className="t1">LDA</th>
                    </tr>
                  </thead>
                  <tbody>
                    {state.data.runwayDirections.map((direction) => {
                      const key = direction.properties.naturalKey;
                      const fields = direction.properties.fields;
                      return (
                        <tr
                          key={key}
                          className="runway-row"
                          onClick={() => onSelect(key)}
                          tabIndex={0}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') onSelect(key);
                          }}
                        >
                          <td className="runway-cell runway-cell-id mono">
                            {String(fields.designator?.value ?? '—')}
                          </td>
                          <NumberCell field={fields.trueBearing} />
                          <td className="runway-cell mono">
                            {fields.length?.value === undefined
                              ? '—'
                              : `${String(fields.length.value)} × ${String(fields.width?.value ?? '—')}`}
                          </td>
                          <td className="runway-cell">{String(fields.surface?.value ?? '—')}</td>
                          <NumberCell field={fields.tora} />
                          <NumberCell field={fields.toda} />
                          <NumberCell field={fields.asda} />
                          <NumberCell field={fields.lda} />
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <p className="airport-card-note t1 mono">
                  TORA / TODA / ASDA / LDA in metres · {String(
                    fieldOf(state.data.runwayDirections[0] ?? null, 'tora')?.section ?? 'AD 2.13'
                  )}
                </p>
                {state.data.runwayDirections.some((d) => textOf(d, 'remarks') !== null) ? (
                  <ul className="runway-remarks">
                    {state.data.runwayDirections.map((direction) => {
                      const remarks = textOf(direction, 'remarks');
                      if (remarks === null || remarks === 'NIL') return null;
                      return (
                        <li key={direction.properties.naturalKey} className="t2">
                          <span className="mono">{String(direction.properties.fields.designator?.value ?? '')}</span>{' '}
                          {remarks}
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </section>

              <section className="airport-card" aria-label="Operational hours">
                <h3 className="airport-card-title t1">Operational hours</h3>
                {HOUR_FIELDS.map((entry) => (
                  <SummaryRow key={entry.key} label={entry.label} field={fieldOf(aerodrome, entry.key)} />
                ))}
              </section>

              <section className="airport-card" aria-label="Contacts">
                <h3 className="airport-card-title t1">Operator &amp; contacts</h3>
                {CONTACT_FIELDS.map((entry) => (
                  <SummaryRow key={entry.key} label={entry.label} field={fieldOf(aerodrome, entry.key)} />
                ))}
              </section>

              <section className="airport-card airport-card-wide" aria-label="Rescue and fire fighting">
                <h3 className="airport-card-title t1">Rescue &amp; fire fighting</h3>
                <div className="summary-inline">
                  <InlineField label="Category" field={fieldOf(aerodrome, 'rffsCategory')} />
                  <InlineField label="Equipment" field={fieldOf(aerodrome, 'rffsEquipment')} />
                  <InlineField
                    label="Disabled aircraft"
                    field={fieldOf(aerodrome, 'disabledAircraftRemoval')}
                  />
                </div>
              </section>
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}
