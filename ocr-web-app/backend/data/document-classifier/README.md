# Receipt document classifier

`model.joblib` is a small scikit-learn 1.7.2 TF-IDF + LogisticRegression artifact.
It was trained only on the labelled training split of expense_classification_clean_v4.jsonl.
It uses input-derived category proxies, merchant, and item names; never gold categories.
It is **REVIEW-only**: no class met validation precision/support requirements.
Do not interpret synthetic evaluation as production validation.

See [analysis and evaluation](../../../docs/receipt-document-classifier.md) and
[machine-readable metrics](../../../reports/document-classifier-evaluation.json).
The full source dataset remains external; its SHA-256 and split IDs are in the report.

Rebuild from backend: `python scripts/train_receipt_document_classifier.py --data PATH`.
Install backend requirements, ship this directory with the backend, and restart workers
after replacing the artifact. Only deploy artifacts from trusted training runs.
