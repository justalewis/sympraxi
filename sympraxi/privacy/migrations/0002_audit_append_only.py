"""Make the audit log append-only at the database level.

UPDATE is always rejected. DELETE is rejected unless the transaction has set
`SET LOCAL sympraxi.audit_purge = 'on'`, which only the retention purge does.
"""

from django.db import migrations

FORWARD = """
CREATE FUNCTION privacy_auditevent_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('sympraxi.audit_purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'privacy_auditevent is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER privacy_auditevent_append_only
    BEFORE UPDATE OR DELETE ON privacy_auditevent
    FOR EACH ROW EXECUTE FUNCTION privacy_auditevent_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS privacy_auditevent_append_only ON privacy_auditevent;
DROP FUNCTION IF EXISTS privacy_auditevent_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("privacy", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(FORWARD, REVERSE),
    ]
