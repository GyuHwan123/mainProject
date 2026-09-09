const clean = (value) => typeof value === 'string' ? value.trim() : '';

export function documentSummary(value) {
  let parsed = value;
  if (typeof value === 'string') {
    try { parsed = JSON.parse(value.replace(/^```(?:json)?\s*|\s*```$/g, '').trim()); } catch { /* Legacy cached prose. */ }
  }
  if (parsed && typeof parsed === 'object') {
    return {
      title: clean(parsed.summary_title),
      lead: clean(parsed.one_line_summary),
      points: Array.isArray(parsed.key_points) ? parsed.key_points.map(clean).filter(Boolean).slice(0, 5) : [],
    };
  }
  const sentences = clean(value).split(/\n+|(?<=[.!?。])\s+/).map(line => line.replace(/^\s*(?:[-•*]|\d+[.)])\s*/, '').trim()).filter(Boolean);
  // Preserve all cached content while distributing sentences across at most five bullets.
  const remaining = sentences.slice(1);
  const size = Math.max(1, Math.ceil(remaining.length / 5));
  return {
    title: '',
    lead: sentences[0] || '',
    points: Array.from({ length: Math.ceil(remaining.length / size) }, (_, i) => remaining.slice(i * size, (i + 1) * size).join(' ')),
  };
}
