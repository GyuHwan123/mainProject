const isMissing = (value) => value == null || String(value).trim() === '';

export function canApplyFinanceTaxSplit(draft) {
  return Boolean(draft && isMissing(draft.supply_amount) && isMissing(draft.tax_amount)
    && !isMissing(draft.total_amount) && Number.isFinite(Number(draft.total_amount))
    && Number(draft.total_amount) > 0);
}

export function applyFinanceTaxSplit(draft, taxRate = 0.1) {
  if (!canApplyFinanceTaxSplit(draft) || ![0, 0.1].includes(taxRate)) return draft;
  const total = Number(draft.total_amount);
  const tax = Math.round(total * taxRate);
  return { ...draft, tax_amount: String(tax), supply_amount: String(total - tax) };
}
