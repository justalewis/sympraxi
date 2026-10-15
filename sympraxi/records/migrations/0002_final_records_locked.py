"""Lock final session records at the database level.

Once a record is final:

- UPDATE of the record is refused unless this transaction has opened an
  amendment for it: `services.amend` writes an `Amendment` row, then sets
  `sympraxi.amendment` to that row's ID with `set_config(..., true)` (local to
  the transaction). Even then the student, tutor, appointment and finalization
  can't change.
- INSERT, UPDATE and DELETE of its topic tags and attached resources follow the
  same rule.
- DELETE of the record is refused.

Amendments are append-only. A future retention purge will set
`SET LOCAL sympraxi.records_purge = 'on'` for its own transaction, the way the
audit log's purge does.
"""

from django.db import migrations

FORWARD = """
CREATE FUNCTION records_purging() RETURNS boolean AS $$
    SELECT COALESCE(current_setting('sympraxi.records_purge', true), '') = 'on';
$$ LANGUAGE sql STABLE;

CREATE FUNCTION records_amendment_open(record uuid) RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1 FROM records_amendment
        WHERE id::text = COALESCE(current_setting('sympraxi.amendment', true), '')
          AND record_id = record
    );
$$ LANGUAGE sql STABLE;

CREATE FUNCTION records_sessionrecord_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'final' AND NOT records_purging() THEN
            RAISE EXCEPTION 'A final session record can''t be deleted (record %)', OLD.id;
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'final' THEN
        RETURN NEW;
    END IF;
    IF NEW.status <> 'final'
        OR NEW.id <> OLD.id
        OR NEW.center_id <> OLD.center_id
        OR NEW.student_id <> OLD.student_id
        OR NEW.tutor_id <> OLD.tutor_id
        OR NEW.appointment_id IS DISTINCT FROM OLD.appointment_id
        OR NEW.created_by_id <> OLD.created_by_id
        OR NEW.created_at <> OLD.created_at
        OR NEW.finalized_at IS DISTINCT FROM OLD.finalized_at
        OR NEW.finalized_by_id IS DISTINCT FROM OLD.finalized_by_id
        OR NEW.copied_from_id IS DISTINCT FROM OLD.copied_from_id
    THEN
        RAISE EXCEPTION
            'A final session record keeps its student, tutor, appointment and finalization (record %)',
            OLD.id;
    END IF;
    IF NOT records_amendment_open(OLD.id) THEN
        RAISE EXCEPTION
            'A final session record can only change through an amendment (record %)', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER records_sessionrecord_guard
    BEFORE UPDATE OR DELETE ON records_sessionrecord
    FOR EACH ROW EXECUTE FUNCTION records_sessionrecord_guard();

CREATE FUNCTION records_require_amendment(record uuid) RETURNS void AS $$
BEGIN
    IF records_purging() THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM records_sessionrecord WHERE id = record AND status = 'final')
        AND NOT records_amendment_open(record)
    THEN
        RAISE EXCEPTION
            'Topics and resources on a final session record can only change through an amendment (record %)',
            record;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION records_child_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM records_require_amendment(OLD.record_id);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM records_require_amendment(NEW.record_id);
        RETURN NEW;
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER records_recordtopic_guard
    BEFORE INSERT OR UPDATE OR DELETE ON records_recordtopic
    FOR EACH ROW EXECUTE FUNCTION records_child_guard();

CREATE TRIGGER records_attachedresource_guard
    BEFORE INSERT OR UPDATE OR DELETE ON records_attachedresource
    FOR EACH ROW EXECUTE FUNCTION records_child_guard();

CREATE FUNCTION records_amendment_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND records_purging() THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'records_amendment is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER records_amendment_append_only
    BEFORE UPDATE OR DELETE ON records_amendment
    FOR EACH ROW EXECUTE FUNCTION records_amendment_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS records_amendment_append_only ON records_amendment;
DROP FUNCTION IF EXISTS records_amendment_append_only();
DROP TRIGGER IF EXISTS records_attachedresource_guard ON records_attachedresource;
DROP TRIGGER IF EXISTS records_recordtopic_guard ON records_recordtopic;
DROP FUNCTION IF EXISTS records_child_guard();
DROP FUNCTION IF EXISTS records_require_amendment(uuid);
DROP TRIGGER IF EXISTS records_sessionrecord_guard ON records_sessionrecord;
DROP FUNCTION IF EXISTS records_sessionrecord_guard();
DROP FUNCTION IF EXISTS records_amendment_open(uuid);
DROP FUNCTION IF EXISTS records_purging();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(FORWARD, REVERSE),
    ]
