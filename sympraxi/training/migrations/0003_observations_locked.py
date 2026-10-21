"""Lock submitted observations at the database level.

Once an observation is submitted it can't be changed or deleted, and neither can its
ratings; a tutor reads what the observer submitted. A draft changes freely. Deleting a
tutor's account removes their observations with it, which is a purge, so it sets
`SET LOCAL sympraxi.training_purge = 'on'` for its own transaction.
"""

from django.db import migrations

FORWARD = """
CREATE FUNCTION training_purging() RETURNS boolean AS $$
    SELECT COALESCE(current_setting('sympraxi.training_purge', true), '') = 'on';
$$ LANGUAGE sql STABLE;

CREATE FUNCTION training_observation_guard() RETURNS trigger AS $$
BEGIN
    IF training_purging() THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'submitted' THEN
            RAISE EXCEPTION 'A submitted observation can''t be deleted (observation %)', OLD.id;
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status = 'submitted' THEN
        RAISE EXCEPTION 'A submitted observation can''t be changed (observation %)', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER training_observation_guard
    BEFORE UPDATE OR DELETE ON training_observation
    FOR EACH ROW EXECUTE FUNCTION training_observation_guard();

CREATE FUNCTION training_rating_guard() RETURNS trigger AS $$
DECLARE
    locked boolean;
BEGIN
    IF training_purging() THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    SELECT status = 'submitted' INTO locked FROM training_observation
        WHERE id = COALESCE(NEW.observation_id, OLD.observation_id);
    IF COALESCE(locked, false) AND TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'The ratings on a submitted observation can''t be changed';
    END IF;
    IF COALESCE(locked, false) AND TG_OP = 'INSERT' THEN
        RAISE EXCEPTION 'The ratings on a submitted observation can''t be changed';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER training_rating_guard
    BEFORE INSERT OR UPDATE OR DELETE ON training_rating
    FOR EACH ROW EXECUTE FUNCTION training_rating_guard();
"""

REVERSE = """
DROP TRIGGER training_rating_guard ON training_rating;
DROP FUNCTION training_rating_guard();
DROP TRIGGER training_observation_guard ON training_observation;
DROP FUNCTION training_observation_guard();
DROP FUNCTION training_purging();
"""


class Migration(migrations.Migration):
    dependencies = [("training", "0002_onboarding")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
