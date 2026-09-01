-- Apply before deploying receipt-v5-sft-canonical-taxonomy.
-- Existing legacy values are deliberately preserved; only new writes are
-- constrained after old rows have been reviewed/migrated separately.
alter table public.finance_records
  alter column document_type drop not null,
  alter column expense_category drop not null,
  alter column expense_category drop default;

alter table public.finance_records
  drop constraint if exists finance_records_expense_category_check;

alter table public.finance_records
  add constraint finance_records_expense_category_check
  check (
    expense_category is null or expense_category in (
      '교통비', '도서인쇄비', '복리후생비(간식)', '복리후생비(식대)', '비품비',
      '소모품비', '여비교통비', '운반비', '인쇄비', '지급수수료', '차량유지비',
      '출장숙박비', '출장식비', '통신비', '회의비'
    )
  ) not valid;

-- Validate only after legacy rows have been audited and normalized.
-- alter table public.finance_records validate constraint finance_records_expense_category_check;
