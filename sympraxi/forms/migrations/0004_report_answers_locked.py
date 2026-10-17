"""Session-report answers follow their record; survey responses stay anonymous.

Responses and answers stay append-only (migration 0002), with one exception:
the answers a tutor gives in a session record.

- While the record is a draft, its response and answers can be replaced. Each
  save of the draft deletes the record's response and writes a new one.
- Once the record is final, its answers change only in a transaction that has
  opened an amendment for that record, the way records migration 0002 locks the
  record itself (`records_amendment_open`). The response itself never changes.
- A session-report response must belong to a record about the same student, in
  the same center and appointment, and only session-report responses belong to
  a record.

A survey response must be anonymous: no student, appointment, record or person
who answered, and no time, only the date. Every other kind of response must say
who answered and when. Used survey links (`forms_usedsurveytoken`) are
append-only, so a link can't be made to work again.

The retention purge (`sympraxi.forms_purge`) is unchanged.
"""

from django.db import migrations

FORWARD = """
DROP TRIGGER forms_response_append_only ON forms_response;
DROP TRIGGER forms_answer_append_only ON forms_answer;
DROP TRIGGER forms_answer_check ON forms_answer;
DROP FUNCTION forms_answer_check();
DROP FUNCTION forms_append_only();

-- The record isn't final: it is a draft, or a draft deleted earlier in this
-- transaction (final records can't be deleted, records migration 0002). Django
-- may delete a draft record before its response, since `record_id` can be null.
CREATE FUNCTION forms_record_draft(record uuid) RETURNS boolean AS $$
    SELECT NOT EXISTS (
        SELECT 1 FROM records_sessionrecord WHERE id = record AND status = 'final'
    );
$$ LANGUAGE sql STABLE;

-- The record's answers may change: it isn't final, or this transaction has
-- opened an amendment to it.
CREATE FUNCTION forms_record_open(record uuid) RETURNS boolean AS $$
    SELECT forms_record_draft(record) OR records_amendment_open(record);
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION forms_response_check() RETURNS trigger AS $$
DECLARE
    form_kind text;
BEGIN
    SELECT f.kind INTO form_kind
    FROM forms_formversion v
    JOIN forms_form f ON f.id = v.form_id
    WHERE v.id = NEW.version_id
      AND v.form_id = NEW.form_id
      AND f.center_id = NEW.center_id
      AND v.published_at IS NOT NULL;
    IF form_kind IS NULL THEN
        RAISE EXCEPTION 'A response must answer a published version of its own form';
    END IF;
    IF form_kind = 'survey' THEN
        IF NEW.student_id IS NOT NULL
            OR NEW.appointment_id IS NOT NULL
            OR NEW.record_id IS NOT NULL
            OR NEW.answered_by_id IS NOT NULL
            OR NEW.created_at IS NOT NULL
            OR NEW.answered_on IS NULL
        THEN
            RAISE EXCEPTION
                'A survey response keeps no person, appointment, record or time, only its date';
        END IF;
    ELSIF NEW.answered_by_id IS NULL OR NEW.created_at IS NULL OR NEW.answered_on IS NOT NULL THEN
        RAISE EXCEPTION 'Only survey responses are anonymous';
    END IF;
    IF form_kind = 'report' THEN
        IF NOT EXISTS (
            SELECT 1 FROM records_sessionrecord r
            WHERE r.id = NEW.record_id
              AND r.student_id = NEW.student_id
              AND r.center_id = NEW.center_id
              AND r.appointment_id IS NOT DISTINCT FROM NEW.appointment_id
        ) THEN
            RAISE EXCEPTION
                'A session-report response must belong to a record about the same student';
        END IF;
        IF NOT forms_record_open(NEW.record_id) THEN
            RAISE EXCEPTION
                'Answers on a final session record can only change through an amendment (record %)',
                NEW.record_id;
        END IF;
    ELSIF NEW.record_id IS NOT NULL THEN
        RAISE EXCEPTION 'Only session-report responses belong to a session record';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION forms_response_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND (
        forms_purging() OR (OLD.record_id IS NOT NULL AND forms_record_draft(OLD.record_id))
    ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'forms_response is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER forms_response_guard
    BEFORE UPDATE OR DELETE ON forms_response
    FOR EACH ROW EXECUTE FUNCTION forms_response_guard();

CREATE FUNCTION forms_answer_guard() RETURNS trigger AS $$
DECLARE
    owner uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF forms_purging() THEN
            RETURN OLD;
        END IF;
        SELECT record_id INTO owner FROM forms_response WHERE id = OLD.response_id;
        IF owner IS NOT NULL AND forms_record_open(owner) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'forms_answer is append-only: DELETE is not allowed';
    END IF;

    SELECT record_id INTO owner FROM forms_response WHERE id = NEW.response_id;
    IF TG_OP = 'UPDATE' THEN
        IF owner IS NULL OR NOT forms_record_open(owner) THEN
            RAISE EXCEPTION 'forms_answer is append-only: UPDATE is not allowed';
        END IF;
        IF NEW.id <> OLD.id
            OR NEW.response_id <> OLD.response_id
            OR NEW.question_id <> OLD.question_id
            OR NEW.key <> OLD.key
        THEN
            RAISE EXCEPTION 'An answer keeps its response and its question';
        END IF;
    ELSIF owner IS NOT NULL AND NOT forms_record_open(owner) THEN
        RAISE EXCEPTION
            'Answers on a final session record can only change through an amendment (record %)',
            owner;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM forms_question q
        JOIN forms_response r ON r.version_id = q.version_id
        WHERE q.id = NEW.question_id
          AND r.id = NEW.response_id
          AND q.key = NEW.key
    ) THEN
        RAISE EXCEPTION
            'An answer must answer a question in its response''s version, under that question''s key';
    END IF;
    IF EXISTS (
        SELECT 1 FROM unnest(NEW.options) AS chosen(option_key)
        WHERE NOT EXISTS (
            SELECT 1 FROM forms_option o
            WHERE o.question_id = NEW.question_id AND o.key = chosen.option_key
        )
    ) THEN
        RAISE EXCEPTION 'An answer can only choose its own question''s options';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER forms_answer_guard
    BEFORE INSERT OR UPDATE OR DELETE ON forms_answer
    FOR EACH ROW EXECUTE FUNCTION forms_answer_guard();

CREATE FUNCTION forms_used_token_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND forms_purging() THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'forms_usedsurveytoken is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER forms_used_token_append_only
    BEFORE UPDATE OR DELETE ON forms_usedsurveytoken
    FOR EACH ROW EXECUTE FUNCTION forms_used_token_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS forms_used_token_append_only ON forms_usedsurveytoken;
DROP FUNCTION IF EXISTS forms_used_token_append_only();
DROP TRIGGER IF EXISTS forms_answer_guard ON forms_answer;
DROP FUNCTION IF EXISTS forms_answer_guard();
DROP TRIGGER IF EXISTS forms_response_guard ON forms_response;
DROP FUNCTION IF EXISTS forms_response_guard();

CREATE OR REPLACE FUNCTION forms_response_check() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM forms_formversion v
        JOIN forms_form f ON f.id = v.form_id
        WHERE v.id = NEW.version_id
          AND v.form_id = NEW.form_id
          AND f.center_id = NEW.center_id
          AND v.published_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'A response must answer a published version of its own form';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP FUNCTION IF EXISTS forms_record_draft(uuid);
DROP FUNCTION IF EXISTS forms_record_open(uuid);

CREATE FUNCTION forms_answer_check() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM forms_question q
        JOIN forms_response r ON r.version_id = q.version_id
        WHERE q.id = NEW.question_id
          AND r.id = NEW.response_id
          AND q.key = NEW.key
    ) THEN
        RAISE EXCEPTION
            'An answer must answer a question in its response''s version, under that question''s key';
    END IF;
    IF EXISTS (
        SELECT 1 FROM unnest(NEW.options) AS chosen(option_key)
        WHERE NOT EXISTS (
            SELECT 1 FROM forms_option o
            WHERE o.question_id = NEW.question_id AND o.key = chosen.option_key
        )
    ) THEN
        RAISE EXCEPTION 'An answer can only choose its own question''s options';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER forms_answer_check
    BEFORE INSERT ON forms_answer
    FOR EACH ROW EXECUTE FUNCTION forms_answer_check();

CREATE FUNCTION forms_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND forms_purging() THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER forms_response_append_only
    BEFORE UPDATE OR DELETE ON forms_response
    FOR EACH ROW EXECUTE FUNCTION forms_append_only();

CREATE TRIGGER forms_answer_append_only
    BEFORE UPDATE OR DELETE ON forms_answer
    FOR EACH ROW EXECUTE FUNCTION forms_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("forms", "0003_report_answers_and_surveys"),
        ("records", "0002_final_records_locked"),
    ]

    operations = [
        migrations.RunSQL(FORWARD, REVERSE),
    ]
