// Open native details for printing, then restore the user's screen state.
export function prepareRagReportPrint(report) {
  const states = [...report.querySelectorAll('.rag-monitoring details')].map(element => [element, element.open]);
  states.forEach(([element]) => { element.open = true; });
  return () => states.forEach(([element, open]) => { element.open = open; });
}
