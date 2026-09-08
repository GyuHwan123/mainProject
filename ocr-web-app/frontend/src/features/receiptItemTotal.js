export function receiptItemTotalCheck(items = [], totalAmount) {
  const hasAmount = (value) => value !== null && value !== undefined && String(value).trim() !== '' && Number.isFinite(Number(value));
  const complete = items.length > 0 && items.every((item) => hasAmount(item.total_amount)) && hasAmount(totalAmount);
  const itemTotal = items.reduce((sum, item) => sum + (hasAmount(item.total_amount) ? Number(item.total_amount) : 0), 0);
  const difference = complete ? itemTotal - Number(totalAmount) : null;
  return { complete, itemTotal, difference, matches: complete && Math.abs(difference) < 0.01 };
}
