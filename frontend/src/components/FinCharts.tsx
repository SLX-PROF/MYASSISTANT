import { useMemo, useState } from "react";
import { MONTHS, rub, type FinMonth, type FinShare } from "../lib/modules";

/* Validated categorical order (neighbours distinguishable, also with color
   blindness). Used only to resolve two slices that share a stored color. */
const SLOTS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const OTHER = "#64748b";
const MAX_SLICES = 5;
export const INCOME_COLOR = "#3987e5";
export const EXPENSE_COLOR = "#d95926";

type Slice = { key: string; name: string; color: string; amount: number; pct: number };

/** Top categories + «Остальное»; colors follow the category, duplicates get a free slot. */
function slices(rows: FinShare[]): Slice[] {
  const total = rows.reduce((a, r) => a + r.amount, 0) || 1;
  const top = rows.slice(0, rows.length > MAX_SLICES + 1 ? MAX_SLICES : MAX_SLICES + 1);
  const rest = rows.slice(top.length);
  const used = new Set<string>();
  const out: Slice[] = top.map((r) => {
    let color = r.id === null ? OTHER : r.color.toLowerCase();
    if (used.has(color)) color = SLOTS.find((c) => !used.has(c)) ?? OTHER;
    used.add(color);
    return { key: String(r.id), name: r.name, color, amount: r.amount, pct: r.amount / total };
  });
  if (rest.length) {
    const amount = rest.reduce((a, r) => a + r.amount, 0);
    out.push({ key: "rest", name: `Остальное (${rest.length})`, color: OTHER, amount, pct: amount / total });
  }
  return out;
}

const pctText = (p: number) => (p < 0.01 ? "<1%" : `${Math.round(p * 100)}%`);

export function Donut({ rows, total, label }: { rows: FinShare[]; total: number; label: string }) {
  const data = useMemo(() => slices(rows), [rows]);
  const [active, setActive] = useState<string | null>(null);
  const cur = data.find((d) => d.key === active) ?? null;
  const R = 70;
  const W = 22;
  const C = 2 * Math.PI * R;
  const gap = data.length > 1 ? 2 : 0; // 2px surface gap between segments
  let offset = 0;
  return (
    <div className="fin-donut">
      <svg viewBox="0 0 180 180" width="180" height="180" role="img" aria-label={`${label}: ${data.map((d) => `${d.name} ${pctText(d.pct)}`).join(", ")}`}>
        <circle cx="90" cy="90" r={R} fill="none" stroke="var(--border)" strokeWidth={W} />
        {data.map((d) => {
          const len = Math.max(0, d.pct * C - gap);
          const el = (
            <circle
              key={d.key}
              cx="90"
              cy="90"
              r={R}
              fill="none"
              stroke={d.color}
              strokeWidth={active === d.key ? W + 6 : W}
              strokeDasharray={`${len} ${C - len}`}
              strokeDashoffset={-offset}
              transform="rotate(-90 90 90)"
              opacity={active && active !== d.key ? 0.35 : 1}
              className="fin-donut-seg"
              onMouseEnter={() => setActive(d.key)}
              onMouseLeave={() => setActive(null)}
              onClick={() => setActive(d.key)}
            />
          );
          offset += d.pct * C;
          return el;
        })}
        <text x="90" y={cur ? 80 : 86} textAnchor="middle" className="fin-donut-k">
          {cur ? cur.name.slice(0, 18) : label}
        </text>
        <text x="90" y={cur ? 102 : 108} textAnchor="middle" className="fin-donut-v">
          {rub(cur ? cur.amount : total)}
        </text>
        {cur && (
          <text x="90" y="120" textAnchor="middle" className="fin-donut-k">
            {pctText(cur.pct)}
          </text>
        )}
      </svg>
      <ul className="fin-legend">
        {data.map((d) => (
          <li key={d.key}>
            <button
              type="button"
              className={active === d.key ? "on" : ""}
              onMouseEnter={() => setActive(d.key)}
              onMouseLeave={() => setActive(null)}
              onClick={() => setActive(d.key)}
            >
              <span className="fin-dot" style={{ background: d.color }} />
              <span className="fin-legend-name">{d.name}</span>
              <span className="fin-legend-pct">{pctText(d.pct)}</span>
              <span className="fin-legend-sum">{rub(d.amount)}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function short(kop: number): string {
  const r = kop / 100;
  if (r >= 1_000_000) return `${(r / 1_000_000).toFixed(r >= 10_000_000 ? 0 : 1).replace(".", ",")} млн`;
  if (r >= 1000) return `${Math.round(r / 1000)} тыс`;
  return `${Math.round(r)}`;
}

function niceMax(v: number): number {
  if (v <= 0) return 100_00;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

/** Income and expense per month: grouped bars, one axis, tap a month for numbers. */
export function MonthBars({ data, current, onPick }: { data: FinMonth[]; current: string; onPick: (month: string) => void }) {
  const [hover, setHover] = useState<string | null>(null);
  const max = niceMax(Math.max(...data.map((d) => Math.max(d.income, d.expense)), 0));
  const H = 150;
  const top = 8;
  const W = 320;
  const left = 54;
  const band = (W - left) / Math.max(data.length, 1);
  const bw = Math.min(16, (band - 14) / 2);
  const y = (v: number) => top + H - (v / max) * H;
  const shown = data.find((d) => d.month === (hover ?? current)) ?? data[data.length - 1];
  const name = (m: string) => MONTHS[Number(m.slice(5)) - 1];
  const bar = (x: number, v: number, color: string, k: string) => {
    const h = (v / max) * H;
    if (h <= 0) return null;
    const r = Math.min(4, h, bw / 2);
    const yy = top + H - h;
    // rounded data-end, square at the baseline
    const d = `M${x},${top + H} V${yy + r} Q${x},${yy} ${x + r},${yy} H${x + bw - r} Q${x + bw},${yy} ${x + bw},${yy + r} V${top + H} Z`;
    return <path key={k} d={d} fill={color} />;
  };
  return (
    <div className="fin-bars">
      <div className="fin-bars-head">
        <span className="fin-legend-inline">
          <span className="fin-dot" style={{ background: INCOME_COLOR }} />
          Доходы
          <span className="fin-dot" style={{ background: EXPENSE_COLOR, marginLeft: 12 }} />
          Расходы
        </span>
      </div>
      {shown && (
        <div className="fin-bars-tip" aria-live="polite">
          <b>
            {name(shown.month)} {shown.month.slice(0, 4)}
          </b>
          <span>
            Доходы <b>{rub(shown.income)}</b>
          </span>
          <span>
            Расходы <b>{rub(shown.expense)}</b>
          </span>
          <span>
            Итог{" "}
            <b className={shown.net < 0 ? "neg" : "pos"}>
              {shown.net < 0 ? "−" : "+"}
              {rub(Math.abs(shown.net))}
            </b>
          </span>
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${top + H + 22}`} className="fin-bars-svg" role="img" aria-label="Доходы и расходы по месяцам">
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={left} x2={W} y1={y(max * f)} y2={y(max * f)} className="fin-grid" />
            <text x={left - 6} y={y(max * f) + 4} textAnchor="end" className="fin-axis">
              {short(max * f)}
            </text>
          </g>
        ))}
        {data.map((d, i) => {
          const x0 = left + i * band + (band - bw * 2 - 2) / 2;
          const on = d.month === (hover ?? current);
          return (
            <g
              key={d.month}
              className="fin-bars-col"
              onMouseEnter={() => setHover(d.month)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onPick(d.month)}
              opacity={on ? 1 : 0.55}
            >
              <rect x={left + i * band} y={top} width={band} height={H + 22} fill="transparent" />
              {bar(x0, d.income, INCOME_COLOR, "i")}
              {bar(x0 + bw + 2, d.expense, EXPENSE_COLOR, "e")}
              <text x={left + i * band + band / 2} y={top + H + 16} textAnchor="middle" className={`fin-axis ${d.month === current ? "cur" : ""}`}>
                {name(d.month).slice(0, 3)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
