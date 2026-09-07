-- 03-finance-records-soft-delete.sql 적용 후 실행.
BEGIN;
CREATE TABLE IF NOT EXISTS public.finance_email_reviews (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 token_hash text UNIQUE NOT NULL,
 sender_email text NOT NULL,
 recipient text NOT NULL,
 records jsonb NOT NULL,
 expires_at timestamptz NOT NULL,
 sent_at timestamptz,
 confirmed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.finance_email_reviews ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.finance_email_reviews FROM anon, authenticated;
GRANT ALL ON public.finance_email_reviews TO service_role;

CREATE OR REPLACE FUNCTION public.confirm_finance_email_review(p_token_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE batch public.finance_email_reviews; item jsonb; done_at timestamptz := now();
BEGIN
 SELECT * INTO batch FROM public.finance_email_reviews WHERE token_hash = p_token_hash FOR UPDATE;
 IF NOT FOUND OR batch.sent_at IS NULL OR batch.expires_at <= now() THEN
   RAISE EXCEPTION 'Invalid or expired review link';
 END IF;
 IF batch.confirmed_at IS NOT NULL THEN
   RETURN jsonb_build_object('confirmed_at', batch.confirmed_at);
 END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(batch.records) LOOP
   UPDATE public.finance_records SET structured_data = jsonb_set(
       structured_data, '{finance_workflow}',
       COALESCE(structured_data->'finance_workflow', '{}'::jsonb) || jsonb_build_object(
         'finance_team_status', '확인', 'finance_confirmed_at', done_at,
         'finance_confirmed_by', batch.recipient, 'confirmation_method', 'email_link',
         'confirmation_batch_id', batch.id)), updated_at = done_at
   WHERE id = (item->>'id')::uuid AND user_id = (item->>'user_id')::uuid
     AND deleted_at IS NULL
     AND structured_data->>'excel_saved_at' = item->>'excel_saved_at'
     AND structured_data->'finance_workflow'->>'submitted_at' IS NOT NULL;
   IF NOT FOUND THEN RAISE EXCEPTION 'Record changed or removed; request a new review email'; END IF;
 END LOOP;
 UPDATE public.finance_email_reviews SET confirmed_at = done_at WHERE id = batch.id;
 RETURN jsonb_build_object('confirmed_at', done_at);
END $$;
REVOKE ALL ON FUNCTION public.confirm_finance_email_review(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.confirm_finance_email_review(text) TO service_role;
COMMIT;
NOTIFY pgrst, 'reload schema';
