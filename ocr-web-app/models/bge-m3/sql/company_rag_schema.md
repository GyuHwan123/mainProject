# Company RAG Schema

## 변경 목적

현재 `models/bge-m3`에서 생성된 기업문서 청크와 BGE-M3 임베딩을 Supabase PostgreSQL에 저장하기 위한 SQL 설계 문서입니다.

현재 생성 결과는 다음과 같습니다.

- `company_chunks.pkl`
- `company_embeddings.pkl`
- `company_chunks.json`
- 임베딩 차원: `1024`

기존 프로젝트의 `rag_chunks.embedding`은 `vector(768)`이므로, 기존 RAG 테이블을 삭제한 뒤 동일한 테이블명으로 `vector(1024)` 구조를 다시 생성합니다.

> **중요: 이 문서의 SQL은 작성만 된 상태입니다. 이 작업에서는 Supabase에 연결하지 않았고, SQL을 실행하지 않았으며, DB를 변경하지 않았습니다.**

## 삭제되는 기존 테이블

다음 기존 테이블을 삭제합니다.

- `public.rag_chunks`
- `public.rag_documents`

`rag_chunks`가 `rag_documents`를 참조하므로 자식 테이블인 `rag_chunks`를 먼저 삭제합니다. 기존 벡터 검색 함수가 테이블을 참조할 수 있으므로 함수도 먼저 삭제합니다.

> **경고: `DROP TABLE`은 해당 테이블의 실제 데이터와 인덱스, 정책 및 테이블 객체를 삭제하는 파괴적 명령입니다. 실행 전에 반드시 백업하고, 삭제 대상과 현재 사용 여부를 확인하세요. 아래 SQL을 Supabase SQL Editor에서 실행하면 기존 `rag_documents`와 `rag_chunks` 데이터가 삭제됩니다.**

## 새로 생성되는 테이블 구조

### `public.rag_documents`

| 컬럼 | 타입 | 제약조건 | 설명 |
| --- | --- | --- | --- |
| `id` | `uuid` | PK, 기본값 `gen_random_uuid()` | 내부 문서 식별자 |
| `doc_id` | `text` | `NOT NULL`, `UNIQUE` | 기업문서 카탈로그의 문서 ID |
| `title` | `text` | `NOT NULL` | 문서 제목 |
| `owner` | `text` | `NOT NULL` | 담당 부서 또는 소유자 |
| `security` | `text` | `NOT NULL` | 보안 등급 |
| `version` | `text` | `NOT NULL` | 문서 버전 |
| `effective_date` | `text` | `NOT NULL` | 시행일. 원본 메타데이터 형식을 보존하기 위해 text로 저장 |
| `filename` | `text` | `NOT NULL` | 원본 파일명 |
| `tags` | `text[]` | `NOT NULL`, 기본값 빈 배열 | 문서 태그 목록 |
| `created_at` | `timestamptz` | `NOT NULL`, 기본값 `now()` | 저장 시각 |

### `public.rag_chunks`

| 컬럼 | 타입 | 제약조건 | 설명 |
| --- | --- | --- | --- |
| `id` | `uuid` | PK, 기본값 `gen_random_uuid()` | 청크 식별자 |
| `document_id` | `uuid` | FK, `ON DELETE CASCADE` | `rag_documents.id` 참조 |
| `chunk_index` | `integer` | `NOT NULL`, 0 이상 | 문서 내 청크 순번 |
| `page_number` | `integer` | `NOT NULL`, 1 이상 | PDF 페이지 번호 |
| `content` | `text` | `NOT NULL`, 공백 불가 | 청크 본문 |
| `embedding` | `vector(1024)` | `NOT NULL` | BGE-M3 임베딩 |
| `created_at` | `timestamptz` | `NOT NULL`, 기본값 `now()` | 저장 시각 |

문서별 청크 순서를 보장하기 위해 `(document_id, chunk_index)`에 유니크 제약조건을 둡니다.

## 실행할 전체 SQL

> 아래 블록은 문서화된 실행 대상일 뿐입니다. 현재 작업에서는 실행하지 않습니다.

```sql
-- ============================================================
-- Company RAG schema for BGE-M3 1024-dimensional embeddings
-- Target: Supabase PostgreSQL
-- ============================================================

begin;

-- ------------------------------------------------------------
-- 1. pgvector extension
-- ------------------------------------------------------------
-- Supabase PostgreSQL에서 벡터 타입과 벡터 연산자를 활성화합니다.
create extension if not exists vector;

-- ------------------------------------------------------------
-- 2. Remove the old RAG search function
-- ------------------------------------------------------------
-- 기존 함수가 old rag_chunks 구조를 참조할 수 있으므로 테이블보다 먼저 제거합니다.
-- vector의 차원(768/1024)은 PostgreSQL 함수 식별자에 포함되지 않습니다.
drop function if exists public.match_rag_chunks(vector, uuid, uuid, double precision, integer);
drop function if exists public.match_rag_chunks(vector, double precision, integer);
drop function if exists public.match_rag_chunks(vector, uuid[], double precision, integer);

-- ------------------------------------------------------------
-- 3. Remove old tables
-- ------------------------------------------------------------
-- WARNING: These statements permanently delete existing table data.
-- Child table first: rag_chunks -> rag_documents.
drop table if exists public.rag_chunks;
drop table if exists public.rag_documents;

-- ------------------------------------------------------------
-- 4. Create the document table
-- ------------------------------------------------------------
create table public.rag_documents (
  id uuid primary key default gen_random_uuid(),
  doc_id text not null unique,
  title text not null,
  owner text not null,
  security text not null,
  version text not null,
  effective_date text not null,
  filename text not null,
  tags text[] not null default '{}'::text[],
  created_at timestamptz not null default now()
);

-- ------------------------------------------------------------
-- 5. Create the chunk table
-- ------------------------------------------------------------
create table public.rag_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.rag_documents(id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),
  page_number integer not null check (page_number >= 1),
  content text not null check (length(btrim(content)) > 0),
  document_title text not null,
  section_title text,
  section_path text[] not null default '{}'::text[],
  heading_level smallint check (heading_level is null or heading_level between 1 and 6),
  bbox jsonb,
  embedding vector(1024) not null,
  created_at timestamptz not null default now(),
  constraint uq_rag_chunks_document_index unique (document_id, chunk_index)
);

-- ------------------------------------------------------------
-- 6. Supporting relational indexes
-- ------------------------------------------------------------
create index idx_rag_chunks_document_page_index
  on public.rag_chunks (document_id, page_number, chunk_index);

create index idx_rag_documents_doc_id
  on public.rag_documents (doc_id);

-- ------------------------------------------------------------
-- 7. HNSW cosine-similarity index
-- ------------------------------------------------------------
-- BGE-M3 임베딩이 normalize_embeddings=True로 생성되었더라도
-- cosine distance 연산자를 사용해 검색 의미를 명시합니다.
create index idx_rag_chunks_embedding_hnsw
  on public.rag_chunks
  using hnsw (embedding vector_cosine_ops);

-- ------------------------------------------------------------
-- 8. Cosine-similarity search function
-- ------------------------------------------------------------
-- similarity = 1 - cosine distance
-- match_threshold는 0~1 범위의 유사도 기준입니다.
create or replace function public.match_rag_chunks(
  query_embedding vector(1024),
  allowed_document_ids uuid[],
  match_threshold double precision default 0.35,
  match_count integer default 5
)
returns table (
  id uuid,
  document_id uuid,
  doc_id text,
  title text,
  owner text,
  security text,
  version text,
  effective_date text,
  filename text,
  tags text[],
  chunk_index integer,
  page_number integer,
  content text,
  document_title text,
  section_title text,
  section_path text[],
  heading_level smallint,
  bbox jsonb,
  similarity double precision
)
language sql
stable
security invoker
set search_path = public
as $$
  select
    chunk.id,
    chunk.document_id,
    document.doc_id,
    document.title,
    document.owner,
    document.security,
    document.version,
    document.effective_date,
    document.filename,
    document.tags,
    chunk.chunk_index,
    chunk.page_number,
    chunk.content,
    chunk.document_title,
    chunk.section_title,
    chunk.section_path,
    chunk.heading_level,
    chunk.bbox,
    (1 - (chunk.embedding <=> query_embedding))::double precision as similarity
  from public.rag_chunks as chunk
  join public.rag_documents as document
    on document.id = chunk.document_id
  where chunk.document_id = any(allowed_document_ids)
    and (1 - (chunk.embedding <=> query_embedding)) >= match_threshold
  order by chunk.embedding <=> query_embedding
  limit least(greatest(match_count, 1), 100);
$$;

commit;
```

## cosine similarity 검색 함수 설명

`public.match_rag_chunks()`는 다음 입력을 받습니다.

- `query_embedding`: 검색 질의의 BGE-M3 `vector(1024)` 임베딩
- `allowed_document_ids`: 검색 권한이 확인된 `rag_documents.id` UUID 배열
- `match_threshold`: 반환할 최소 cosine similarity. 기본값 `0.35`
- `match_count`: 반환 개수. 최소 1개, 최대 100개로 제한

pgvector의 `<=>` 연산자는 cosine distance를 반환하므로 함수는 다음 계산을 사용합니다.

```text
cosine similarity = 1 - cosine distance
```

검색 결과에는 청크 정보뿐 아니라 연결된 `rag_documents`의 기업문서 메타데이터도 함께 반환됩니다.

예시 호출 형태:

```sql
select *
from public.match_rag_chunks(
  query_embedding => '[0.01, 0.02, ...]'::vector(1024),
  allowed_document_ids => array['00000000-0000-0000-0000-000000000001']::uuid[],
  match_threshold => 0.35,
  match_count => 5
);
```

`...`는 실제 1024개 실수 값으로 교체해야 합니다.

## vector index 설명

`idx_rag_chunks_embedding_hnsw`는 pgvector의 HNSW 인덱스와 `vector_cosine_ops` 연산 클래스를 사용합니다.

- 용도: cosine distance 기반 최근접 벡터 검색 가속
- 대상: `rag_chunks.embedding`
- 차원: `1024`
- 검색 연산자: `<=>`
- 장점: 데이터가 증가해도 전체 테이블 스캔을 줄일 수 있음
- 주의: 인덱스 생성에는 추가 저장 공간과 생성 시간이 필요함

초기 데이터가 아주 적거나 HNSW 생성이 부담스러운 환경에서는 인덱스 없이 먼저 검증한 뒤 추가해도 됩니다. 다만 이 문서의 전체 SQL에는 운영 검색을 고려해 HNSW 인덱스를 포함했습니다.

## 적용 후 확인용 SELECT 쿼리

아래 쿼리는 스키마 적용 후 Supabase SQL Editor에서 확인용으로 실행할 수 있습니다. 이 문서 작성 작업에서는 실행하지 않았습니다.

```sql
-- 1. pgvector extension 설치 여부
select extname, extversion, extnamespace::regnamespace as installed_schema
from pg_extension
where extname = 'vector';

-- 2. 새 테이블 존재 여부
select table_schema, table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in ('rag_documents', 'rag_chunks')
order by table_name;

-- 3. 컬럼 타입과 차원 확인
select
  table_name,
  column_name,
  data_type,
  udt_name,
  is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name in ('rag_documents', 'rag_chunks')
order by table_name, ordinal_position;

-- 4. embedding이 vector(1024)인지 확인
select
  attrelid::regclass as table_name,
  attname as column_name,
  format_type(atttypid, atttypmod) as column_type
from pg_attribute
where attrelid in ('public.rag_chunks'::regclass)
  and attname = 'embedding'
  and not attisdropped;

-- 5. 외래키와 ON DELETE CASCADE 확인
select
  constraint_name,
  table_name,
  constraint_type
from information_schema.table_constraints
where table_schema = 'public'
  and table_name in ('rag_documents', 'rag_chunks')
order by table_name, constraint_name;

-- 6. 인덱스 확인
select tablename, indexname, indexdef
from pg_indexes
where schemaname = 'public'
  and tablename in ('rag_documents', 'rag_chunks')
order by tablename, indexname;

-- 7. 저장 건수 확인
select 'rag_documents' as table_name, count(*) as row_count
from public.rag_documents
union all
select 'rag_chunks' as table_name, count(*) as row_count
from public.rag_chunks;

-- 8. 검색 함수 존재 여부
select proname, pg_get_function_identity_arguments(oid) as arguments
from pg_proc
where pronamespace = 'public'::regnamespace
  and proname = 'match_rag_chunks';
```

## 적용 순서

1. 기존 `rag_documents`와 `rag_chunks` 데이터를 백업합니다.
2. 현재 테이블과 함수, 인덱스, RLS 정책의 사용 여부를 확인합니다.
3. 이 문서의 전체 SQL을 검토합니다.
4. Supabase SQL Editor에서 `pgvector` 확장 권한과 프로젝트 정책을 확인합니다.
5. 전체 SQL을 한 번에 실행합니다.
6. `적용 후 확인용 SELECT 쿼리`를 실행해 테이블, `vector(1024)`, 외래키, HNSW 인덱스, 함수 존재 여부를 확인합니다.
7. `company_chunks.pkl`과 `company_embeddings.pkl`의 동일한 순서를 유지하여 문서와 청크를 적재합니다.
8. 실제 벡터 검색 전에 소수의 테스트 데이터로 검색 결과와 삭제 cascade 동작을 검증합니다.

## 주의사항

- **`DROP TABLE`은 기존 데이터를 영구 삭제합니다. 백업 없이 실행하지 마세요.**
- 이 문서의 SQL은 작성만 되었으며, Supabase 연결과 DB 변경은 수행하지 않았습니다.
- 기존 `rag_documents`와 `rag_chunks`의 데이터, 인덱스, RLS 정책은 삭제됩니다. 필요한 정책이 있으면 새 테이블에 별도로 재작성해야 합니다.
- 새 `rag_documents` 구조에는 기존 프로젝트의 `user_id`, `document_id`, `status`, `embedding_model`, `chunk_count` 컬럼이 없습니다.
- 따라서 현재 backend의 기존 RAG endpoint와 `SupabaseService.replace_rag_index()`는 이 새 스키마와 호환되지 않습니다. backend를 수정하지 않는 현재 단계에서는 SQL 설계와 데이터 적재 준비까지만 진행됩니다.
- 현재 프로젝트의 기존 backend는 `rag_chunks`에 `vector(768)` 임베딩을 전제로 하므로, 새 스키마 적용 후 기존 backend RAG 인덱싱과 검색은 정상 동작하지 않을 수 있습니다.
- 검색 질의 임베딩도 반드시 BGE-M3와 동일한 모델 및 `1024차원` 설정으로 생성해야 합니다. 다른 임베딩 모델의 벡터를 섞어 저장하거나 검색하면 유사도 결과가 의미를 갖지 않습니다.
- `effective_date`를 PostgreSQL `date`로 강제하지 않고 `text`로 둔 것은 현재 기업문서 메타데이터의 원문 형식을 보존하기 위해서입니다. 날짜 형식이 확정되면 `date` 타입과 유효성 검사를 별도로 검토할 수 있습니다.
- `tags` 적재 시 JSON 배열을 PostgreSQL `text[]` 형식으로 변환해야 합니다.
- `company_embeddings.pkl`의 각 행과 `company_chunks.pkl`의 각 원소는 같은 인덱스의 데이터를 가리켜야 합니다.
- HNSW 인덱스는 데이터가 커질수록 유용하지만, 작은 데이터셋에서는 순차 검색과 성능 차이가 작을 수 있습니다.
- Supabase의 RLS를 사용할 경우 service role key를 사용한 적재 경로와 일반 사용자 검색 경로의 권한을 별도로 설계해야 합니다.
- Docker, `docker-compose`, backend Python 파일은 이 설계 문서 작성에 필요하지 않으며 수정하지 않습니다.
