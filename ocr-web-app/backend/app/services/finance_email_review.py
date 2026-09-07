import hashlib
import secrets
from datetime import datetime, timedelta, timezone
import httpx
from fastapi import HTTPException
from app.core.config import settings
from app.services.supabase_service import supabase_service as db


def _request(method, path, **kwargs):
    response = httpx.request(method, f'{db.url}/rest/v1/{path}', headers={**db._service_headers(), 'Prefer': 'return=representation'}, timeout=20, **kwargs)
    if response.status_code >= 400:
        raise HTTPException(502, '재무팀 검토 정보 처리에 실패했습니다. 기록이 변경되었다면 새 메일을 요청해 주세요.')
    return response.json()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_review(records, user):
    if not settings.FRONTEND_URL:
        raise HTTPException(503, '이메일 검토 링크용 FRONTEND_URL 설정이 필요합니다.')
    token = secrets.token_urlsafe(32)
    owner = db.get_public_user_id(user.email)
    snapshot = [{'id': r['id'], 'user_id': owner, 'merchant': r.get('merchant'),
                 'total_amount': r.get('total_amount'), 'transaction_date': r.get('transaction_date'),
                 'excel_saved_at': (r.get('structured_data') or {}).get('excel_saved_at')} for r in records]
    row = _request('POST', 'finance_email_reviews', json={
        'token_hash': token_hash(token), 'sender_email': user.email, 'recipient': 'docai0914@gmail.com',
        'records': snapshot, 'expires_at': (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    })[0]
    return row['id'], f"{settings.FRONTEND_URL.rstrip('/')}/finance-review#token={token}"


def activate_review(review_id):
    _request('PATCH', 'finance_email_reviews', params={'id': f'eq.{review_id}'}, json={'sent_at': datetime.now(timezone.utc).isoformat()})


def read_review(token):
    rows = _request('GET', 'finance_email_reviews', params={'token_hash': f'eq.{token_hash(token)}', 'select': '*', 'limit': '1'})
    if not rows or not rows[0].get('sent_at') or datetime.fromisoformat(rows[0]['expires_at'].replace('Z', '+00:00')) <= datetime.now(timezone.utc):
        raise HTTPException(410, '유효하지 않거나 만료된 링크입니다. 새 검토 메일을 요청해 주세요.')
    row = rows[0]
    return {key: row[key] for key in ('sender_email', 'recipient', 'records', 'expires_at', 'confirmed_at')}


def confirm_review(token):
    read_review(token)
    return _request('POST', 'rpc/confirm_finance_email_review', json={'p_token_hash': token_hash(token)})
