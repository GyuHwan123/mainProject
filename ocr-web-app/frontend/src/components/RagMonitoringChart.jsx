import React from 'react';

export const RAG_TREND_METRICS = [
  ['answer_accuracy', 'Answer Accuracy', '#079b62'],
  ['faithfulness', 'Faithfulness', '#7c3aed'],
  ['hit_at_1', 'Hit@1', '#f06a13'],
  ['context_precision', 'Context Precision', '#24599b'],
  ['hallucination_rate', 'Hallucination Rate', '#d45764'],
];

export default function RagMonitoringChart({ daily, metric, color = '#1767df' }) {
  const compact = Boolean(metric);
  const series = compact ? [[metric, metric, color]] : RAG_TREND_METRICS;
  const width = compact ? 220 : 620;
  const height = compact ? 38 : 190;
  const left = compact ? 2 : 42;
  const top = compact ? 2 : 10;
  const plotHeight = compact ? 32 : 150;
  const plotWidth = width - left - 10;
  const maximum = metric === 'total' ? Math.max(1, ...daily.map(day => day.total || 0)) : 1;
  const hasData = daily.some(day => day.run_count > 0 && series.some(([key]) => day[key] != null));
  if (!hasData) return <div className={compact ? 'rag-sparkline-slot' : 'empty-monitoring-box'}><span>평가 이력이 없습니다.</span></div>;
  return <div className={compact ? '' : 'daily-performance-chart'}>
    {!compact && <div className="daily-chart-legend">{series.map(([key, label, stroke]) => <span key={key}><i style={{ background: stroke }} />{label}</span>)}</div>}
    <svg className={compact ? 'metric-sparkline' : undefined} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={compact ? `${metric} 일별 추이` : '일별 RAG 평가 성능'}>
      {!compact && [0, .5, 1].map(tick => <g key={tick}><line x1={left} x2={width - 10} y1={top + plotHeight * (1 - tick)} y2={top + plotHeight * (1 - tick)} /><text x="35" y={top + plotHeight * (1 - tick) + 3}>{tick * 100}%</text></g>)}
      {series.map(([key, label, stroke]) => {
        const segments = [[]];
        daily.forEach((day, index) => {
          if (!day.run_count || day[key] == null) { if (segments.at(-1).length) segments.push([]); return; }
          segments.at(-1).push({ day, x: left + (daily.length === 1 ? plotWidth / 2 : index * plotWidth / (daily.length - 1)), y: top + plotHeight * (1 - Math.min(1, day[key] / maximum)) });
        });
        return <g key={key}>{segments.filter(segment => segment.length).map((segment, index) => <g key={index}>
          <polyline fill="none" stroke={stroke} strokeWidth="2" points={segment.map(point => `${point.x},${point.y}`).join(' ')} />
          {segment.map(({ day, x, y }) => <circle key={day.date} cx={x} cy={y} r="2" fill={stroke}><title>{`${day.date} · ${label}: ${key === 'total' ? day[key] : `${(day[key] * 100).toFixed(1)}%`}`}</title></circle>)}
        </g>)}</g>;
      })}
      {!compact && daily.filter((_, index) => index === 0 || index === daily.length - 1 || index % Math.max(1, Math.ceil(daily.length / 6)) === 0).map(day => <text key={day.date} x={left + (daily.length === 1 ? plotWidth / 2 : daily.indexOf(day) * plotWidth / (daily.length - 1))} y="181">{day.date.slice(5)}</text>)}
    </svg>
  </div>;
}
