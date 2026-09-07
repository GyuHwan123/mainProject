-- Supabase SQL Editor에서 애플리케이션 변경 배포 전에 실행하세요.
-- 기존 receipt_archive와 동일하게 deleted_at 이름을 사용합니다.
BEGIN;
ALTER TABLE public.finance_records
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz DEFAULT NULL;
COMMENT ON COLUMN public.finance_records.deleted_at IS
    '소프트 삭제 시각. NULL이면 표시하며, 삭제해도 원본과 발송 이력 데이터는 보존한다.';
CREATE INDEX IF NOT EXISTS finance_records_active_user_created_idx
    ON public.finance_records (user_id, created_at DESC)
    WHERE deleted_at IS NULL;
COMMIT;
NOTIFY pgrst, 'reload schema';

-- 복구 예시 (필요한 ID를 지정한 후 별도로 실행):
-- UPDATE public.finance_records SET deleted_at = NULL WHERE id = '대상 UUID';
