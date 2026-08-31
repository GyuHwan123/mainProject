-- Align existing finance records with receipt_dataset_verified/receipts.json.
-- This intentionally updates only unambiguous legacy labels. Historical
-- evaluation ground truth is not rewritten.

begin;

update public.finance_records
set expense_category = case expense_category
  when '교통비' then '교통'
  when '여비교통비' then '교통'
  when '차량유지비' then '주유/교통'
  when '도서인쇄비' then '도서'
  when '도서인쇄' then '도서'
  when '복리후생비(간식)' then '식비/생활'
  when '복리후생비(식대)' then '식비'
  when '출장식비' then '식비'
  when '출장식대' then '식비'
  when '출장식사' then '식비'
  when '회의비' then '식비'
  when '비품비' then '전자제품/문구'
  when '소모품비' then '전자제품/문구'
  when '비품' then '전자제품/문구'
  when '소모품' then '전자제품/문구'
  when '사무용품' then '전자제품/문구'
  else expense_category
end
where expense_category in (
  '교통비', '여비교통비', '차량유지비', '도서인쇄비', '도서인쇄',
  '복리후생비(간식)', '복리후생비(식대)', '출장식비', '출장식대',
  '출장식사', '회의비', '비품비', '소모품비', '비품', '소모품', '사무용품'
);

commit;

-- Any rows returned here require a manual mapping decision. Do not silently
-- coerce them to another semantic category.
select expense_category, count(*) as record_count
from public.finance_records
where expense_category is not null
  and expense_category not in (
    '취미/쇼핑', '미용', '도서', '전자제품/문구', '교통', '주유/교통',
    '미용/생활', '식비', '레저', '전자제품', '식비/주류', '식비/생활',
    '생활/식비', '의료', '문화', '식비/쇼핑'
  )
group by expense_category
order by record_count desc, expense_category;
