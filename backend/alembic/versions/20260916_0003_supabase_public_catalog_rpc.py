"""Expose least-privilege public catalog RPCs for the Sites Worker."""

from alembic import context, op
from app.config import settings

revision = "20260916_0003"
down_revision = "20260916_0002"
branch_labels = None
depends_on = None


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def upgrade():
    schema = _quoted(context.config.attributes.get("schema", settings.database_schema))
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.scl_search_catalog(
            p_query text,
            p_test_limit integer DEFAULT 8,
            p_public_limit integer DEFAULT 6
        ) RETURNS jsonb
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            normalized_query text;
            query_terms text[];
            test_results jsonb;
            public_results jsonb;
        BEGIN
            normalized_query := lower(regexp_replace(trim(left(coalesce(p_query, ''), 500)), '[^0-9A-Za-z가-힣]+', ' ', 'g'));
            IF normalized_query = '' THEN
                RETURN jsonb_build_object('tests', '[]'::jsonb, 'public_items', '[]'::jsonb);
            END IF;
            SELECT coalesce(array_agg(term), ARRAY[]::text[]) INTO query_terms
            FROM unnest(regexp_split_to_array(normalized_query, '\\s+')) AS term
            WHERE length(term) > 1 AND term <> ALL (ARRAY['검사','알려','주세요','궁금','대한','관련','정보']);

            WITH candidates AS (
                SELECT tv.id, t.source_test_code AS code, tv.source_row_key AS variant_key,
                       tv.display_name AS name,
                       coalesce((SELECT jsonb_agg(a.alias ORDER BY a.id) FROM {schema}.test_aliases a
                                 WHERE a.test_id = t.id AND a.verified), '[]'::jsonb) AS aliases,
                       coalesce(s.canonical_name, '정보 없음') AS specimen,
                       tv.container_text AS container,
                       coalesce(m.name, '정보 없음') AS method,
                       coalesce(tv.schedule_text, '정보 없음') AS schedule,
                       coalesce(tv.tat_text, '정보 없음') AS tat,
                       tv.detail_url AS source_url, tv.last_seen_at AS updated_at,
                       coalesce((SELECT jsonb_object_agg(detail.key, left(detail.value, 700))
                                 FROM jsonb_each_text(coalesce(d.fields_json::jsonb, '{{}}'::jsonb)) detail
                                 WHERE detail.key = ANY (ARRAY['임상적 의의','채취방법 및 주의사항','검사정보','참고치','검사방법'])
                                   AND btrim(detail.value) <> ''), '{{}}'::jsonb) AS public_details,
                       lower(concat_ws(' ', t.source_test_code, tv.display_name, s.canonical_name,
                             tv.container_text, m.name, tv.schedule_text, tv.tat_text, d.full_text,
                             (SELECT string_agg(a.alias, ' ') FROM {schema}.test_aliases a
                              WHERE a.test_id = t.id AND a.verified))) AS search_text
                FROM {schema}.test_variants tv
                JOIN {schema}.tests t ON t.id = tv.test_id
                JOIN {schema}.data_sources ds ON ds.id = t.data_source_id
                LEFT JOIN {schema}.specimens s ON s.id = tv.specimen_id
                LEFT JOIN {schema}.methods m ON m.id = tv.method_id
                LEFT JOIN {schema}.test_public_details d ON d.test_variant_id = tv.id
                WHERE ds.key = 'SCL_PUBLIC' AND t.status = 'active' AND tv.status = 'active'
            ), ranked AS (
                SELECT c.*,
                       (CASE WHEN lower(c.code) = normalized_query THEN 1000 ELSE 0 END
                        + CASE WHEN lower(c.name) = normalized_query THEN 800
                               WHEN lower(c.name) LIKE '%' || normalized_query || '%' OR normalized_query LIKE '%' || lower(c.name) || '%' THEN 300 ELSE 0 END
                        + CASE WHEN lower(c.code) <> normalized_query AND lower(c.code) <> ''
                                    AND normalized_query LIKE '%' || lower(c.code) || '%' THEN 250 ELSE 0 END
                        + 35 * (SELECT count(*) FROM unnest(query_terms) term WHERE c.search_text LIKE '%' || term || '%')
                        + CASE WHEN cardinality(query_terms) > 0 AND
                                    (SELECT count(*) FROM unnest(query_terms) term WHERE c.search_text LIKE '%' || term || '%') = cardinality(query_terms)
                               THEN 120 ELSE 0 END) AS score
                FROM candidates c
            )
            SELECT coalesce(jsonb_agg(jsonb_build_object(
                       'code', code, 'variant_key', variant_key, 'name', name, 'aliases', aliases,
                       'specimen', specimen, 'container', container, 'method', method,
                       'schedule', schedule, 'tat', tat, 'source_title', 'SCL 검사항목조회',
                       'source_url', source_url, 'updated_at', to_char(updated_at, 'YYYY-MM-DD'),
                       'demo', false, 'public_details', public_details, 'score', score
                   ) ORDER BY score DESC, name) FILTER (WHERE score > 0), '[]'::jsonb)
            INTO test_results
            FROM (SELECT * FROM ranked WHERE score > 0 ORDER BY score DESC, name LIMIT least(greatest(coalesce(p_test_limit, 8), 1), 12)) limited;

            WITH public_rows AS (
                SELECT 'location'::text AS entity_type, id, name AS title,
                       concat_ws(' · ', nullif(address, ''), nullif(phone, '')) AS snippet,
                       source_url, last_seen_at AS updated_at
                FROM {schema}.service_locations WHERE status = 'active'
                UNION ALL
                SELECT 'route', id, label, path, source_url, last_seen_at
                FROM {schema}.site_routes WHERE status = 'active'
                UNION ALL
                SELECT 'document', id, title, coalesce(summary, left(body_text, 900)), source_url, updated_at
                FROM {schema}.public_documents WHERE status = 'active'
                UNION ALL
                SELECT 'attachment', da.id, da.file_name, left(ac.extracted_text, 900), pd.source_url, ac.extracted_at
                FROM {schema}.document_attachments da
                JOIN {schema}.attachment_contents ac ON ac.attachment_id = da.id
                JOIN {schema}.public_documents pd ON pd.id = da.document_id
                WHERE da.status = 'active' AND pd.status = 'active'
                  AND ac.extraction_status = 'extracted' AND length(btrim(ac.normalized_text)) > 0
            ), ranked AS (
                SELECT p.*,
                       (CASE WHEN lower(p.title) = normalized_query THEN 700
                              WHEN lower(p.title) LIKE '%' || normalized_query || '%' THEN 260 ELSE 0 END
                        + 24 * (SELECT count(*) FROM unnest(query_terms) term
                                WHERE lower(concat_ws(' ', p.title, p.snippet)) LIKE '%' || term || '%')
                        + CASE WHEN cardinality(query_terms) > 0 AND
                                    (SELECT count(*) FROM unnest(query_terms) term
                                     WHERE lower(concat_ws(' ', p.title, p.snippet)) LIKE '%' || term || '%') = cardinality(query_terms)
                               THEN 90 ELSE 0 END) AS score
                FROM public_rows p
            )
            SELECT coalesce(jsonb_agg(jsonb_build_object(
                       'ref', entity_type || ':' || id, 'entity_type', entity_type,
                       'title', title, 'snippet', coalesce(snippet, ''), 'source_url', source_url,
                       'updated_at', to_char(updated_at, 'YYYY-MM-DD'), 'score', score
                   ) ORDER BY score DESC, title) FILTER (WHERE score > 0), '[]'::jsonb)
            INTO public_results
            FROM (SELECT * FROM ranked WHERE score > 0 ORDER BY score DESC, title LIMIT least(greatest(coalesce(p_public_limit, 6), 1), 12)) limited;

            RETURN jsonb_build_object('tests', test_results, 'public_items', public_results);
        END;
        $$;

        CREATE OR REPLACE FUNCTION public.scl_catalog_status() RETURNS jsonb
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT jsonb_build_object(
                'tests', count(DISTINCT t.source_test_code),
                'variants', count(DISTINCT tv.id),
                'details', count(DISTINCT d.test_variant_id)
            )
            FROM {schema}.tests t
            JOIN {schema}.data_sources ds ON ds.id = t.data_source_id AND ds.key = 'SCL_PUBLIC'
            JOIN {schema}.test_variants tv ON tv.test_id = t.id AND tv.status = 'active'
            LEFT JOIN {schema}.test_public_details d ON d.test_variant_id = tv.id
            WHERE t.status = 'active';
        $$;

        REVOKE ALL ON FUNCTION public.scl_search_catalog(text, integer, integer) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.scl_catalog_status() FROM PUBLIC;
        """
    )
    for role in ("anon", "authenticated", "service_role"):
        op.execute(
            f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    GRANT EXECUTE ON FUNCTION public.scl_search_catalog(text, integer, integer) TO {role};
                    GRANT EXECUTE ON FUNCTION public.scl_catalog_status() TO {role};
                END IF;
            END $$"""
        )


def downgrade():
    op.execute("DROP FUNCTION IF EXISTS public.scl_catalog_status()")
    op.execute("DROP FUNCTION IF EXISTS public.scl_search_catalog(text, integer, integer)")
