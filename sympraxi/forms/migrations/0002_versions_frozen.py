"""Keep form versions and answers honest at the database level.

- A published version can't change or be deleted, and neither can its
  questions or options; it can't gain questions or options either. Publishing
  (setting `published_at` on a draft) is the last change a version takes.
- A response must answer a published version of its own form, in the form's
  center.
- An answer must answer a question in its response's version, stored under that
  question's key, and can choose only that question's options.
- Responses and answers are append-only. New answers are a new response.

A future retention purge will set `SET LOCAL sympraxi.forms_purge = 'on'` for its
own transaction, the way the audit log's and the records' purges do.
"""

from django.db import migrations

FORWARD = """
CREATE FUNCTION forms_purging() RETURNS boolean AS $$
    SELECT COALESCE(current_setting('sympraxi.forms_purge', true), '') = 'on';
$$ LANGUAGE sql STABLE;

CREATE FUNCTION forms_version_published(version uuid) RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1 FROM forms_formversion WHERE id = version AND published_at IS NOT NULL
    );
$$ LANGUAGE sql STABLE;

CREATE FUNCTION forms_formversion_guard() RETURNS trigger AS $$
BEGIN
    IF OLD.published_at IS NULL OR forms_purging() THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'A published form version can''t change or be deleted (version %)', OLD.id;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER forms_formversion_guard
    BEFORE UPDATE OR DELETE ON forms_formversion
    FOR EACH ROW EXECUTE FUNCTION forms_formversion_guard();

CREATE FUNCTION forms_question_guard() RETURNS trigger AS $$
BEGIN
    IF NOT forms_purging() THEN
        IF TG_OP IN ('UPDATE', 'DELETE') AND forms_version_published(OLD.version_id) THEN
            RAISE EXCEPTION
                'Questions in a published form version can''t change (question %)', OLD.id;
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') AND forms_version_published(NEW.version_id) THEN
            RAISE EXCEPTION
                'A published form version can''t gain questions (version %)', NEW.version_id;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER forms_question_guard
    BEFORE INSERT OR UPDATE OR DELETE ON forms_question
    FOR EACH ROW EXECUTE FUNCTION forms_question_guard();

CREATE FUNCTION forms_question_published(question uuid) RETURNS boolean AS $$
    SELECT forms_version_published(
        (SELECT version_id FROM forms_question WHERE id = question)
    );
$$ LANGUAGE sql STABLE;

CREATE FUNCTION forms_option_guard() RETURNS trigger AS $$
BEGIN
    IF NOT forms_purging() THEN
        IF TG_OP IN ('UPDATE', 'DELETE') AND forms_question_published(OLD.question_id) THEN
            RAISE EXCEPTION
                'Options in a published form version can''t change (option %)', OLD.id;
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') AND forms_question_published(NEW.question_id) THEN
            RAISE EXCEPTION
                'A question in a published form version can''t gain options (question %)',
                NEW.question_id;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER forms_option_guard
    BEFORE INSERT OR UPDATE OR DELETE ON forms_option
    FOR EACH ROW EXECUTE FUNCTION forms_option_guard();

CREATE FUNCTION forms_response_check() RETURNS trigger AS $$
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

CREATE TRIGGER forms_response_check
    BEFORE INSERT ON forms_response
    FOR EACH ROW EXECUTE FUNCTION forms_response_check();

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

REVERSE = """
DROP TRIGGER IF EXISTS forms_answer_append_only ON forms_answer;
DROP TRIGGER IF EXISTS forms_response_append_only ON forms_response;
DROP FUNCTION IF EXISTS forms_append_only();
DROP TRIGGER IF EXISTS forms_answer_check ON forms_answer;
DROP FUNCTION IF EXISTS forms_answer_check();
DROP TRIGGER IF EXISTS forms_response_check ON forms_response;
DROP FUNCTION IF EXISTS forms_response_check();
DROP TRIGGER IF EXISTS forms_option_guard ON forms_option;
DROP FUNCTION IF EXISTS forms_option_guard();
DROP FUNCTION IF EXISTS forms_question_published(uuid);
DROP TRIGGER IF EXISTS forms_question_guard ON forms_question;
DROP FUNCTION IF EXISTS forms_question_guard();
DROP TRIGGER IF EXISTS forms_formversion_guard ON forms_formversion;
DROP FUNCTION IF EXISTS forms_formversion_guard();
DROP FUNCTION IF EXISTS forms_version_published(uuid);
DROP FUNCTION IF EXISTS forms_purging();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("forms", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(FORWARD, REVERSE),
    ]
