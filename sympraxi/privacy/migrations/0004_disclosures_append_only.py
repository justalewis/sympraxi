"""Make each student's record of disclosures append-only at the database level.

34 CFR 99.32 asks that the record be kept as long as the education records
are, so an entry is never changed: a mistake is fixed by a new entry that
corrects it. UPDATE is always rejected. DELETE is rejected unless the
transaction has set `SET LOCAL sympraxi.records_purge = 'on'`, which only the
retention purge of the student's records will do.
"""

from django.db import migrations

FORWARD = """
CREATE FUNCTION privacy_disclosure_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('sympraxi.records_purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'privacy_disclosure is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER privacy_disclosure_append_only
    BEFORE UPDATE OR DELETE ON privacy_disclosure
    FOR EACH ROW EXECUTE FUNCTION privacy_disclosure_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS privacy_disclosure_append_only ON privacy_disclosure;
DROP FUNCTION IF EXISTS privacy_disclosure_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("privacy", "0003_disclosure"),
    ]

    operations = [
        migrations.RunSQL(FORWARD, REVERSE),
    ]
