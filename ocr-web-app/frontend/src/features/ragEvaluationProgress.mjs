export function evaluationCompletion(result, total) {
  const completed = result.completed_count ?? result.cases?.length ?? 0;
  const errors = result.error_count ?? Math.max(0, total - completed);
  return { status: errors || completed < total ? 'partial' : 'completed', completed_count: completed, error_count: errors };
}

export function evaluationStatusLabel(progress) {
  if (progress.error_count > 0 && progress.status !== 'running') {
    return `부분 완료 · ${progress.completed_count ?? 0}/${progress.total} · 오류 ${progress.error_count}`;
  }
  if (progress.status === 'partial') return `부분 완료 · ${progress.completed_count ?? 0}/${progress.total}`;
  if (progress.status === 'completed') return `완료 · ${progress.completed_count ?? progress.total}/${progress.total}`;
  return '대기';
}
