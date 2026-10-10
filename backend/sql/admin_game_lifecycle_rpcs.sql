    -- Gameplay configuration and team-change serialization safeguards.
    -- This adds/drops functions and replaces triggers only; it does not create or
    -- alter tables, columns, enums, or existing gameplay history.
    -- Reapply in Supabase SQL Editor to remove the obsolete lifecycle transitions.
-- admin_game_lifecycle_rpcs.sql
    CREATE OR REPLACE FUNCTION public.admin_is_valid_ingredient_order(p_order uuid[])
    RETURNS boolean
    LANGUAGE plpgsql
    STABLE
    AS $$
    DECLARE
        v_distinct_count integer;
        v_active_count integer;
    BEGIN
        IF COALESCE(cardinality(p_order), 0) <> 10 THEN
            RETURN FALSE;
        END IF;

        SELECT count(DISTINCT requested.id), count(ingredient.id)
        INTO v_distinct_count, v_active_count
        FROM unnest(COALESCE(p_order, ARRAY[]::uuid[])) AS requested(id)
        LEFT JOIN public.ingredients AS ingredient
            ON ingredient.id = requested.id
        AND ingredient.is_active IS TRUE;

        RETURN v_distinct_count = 10 AND v_active_count = 10;
    END;
    $$;


    CREATE OR REPLACE FUNCTION public.admin_game_configuration_issues()
    RETURNS text[]
    LANGUAGE plpgsql
    STABLE
    AS $$
    DECLARE
        v_issues text[] := ARRAY[]::text[];
        v_active_count integer;
        v_witch_count integer;
        v_real_count integer;
        v_fake_count integer;
        v_master_order uuid[];
        v_invalid_witch_order boolean;
        v_active_organizer_count integer;
        v_organizer_profile_count integer;
        v_distinct_active_witches integer;
        v_invalid_organizer_mapping boolean;
    BEGIN
        SELECT count(*) INTO v_active_count
        FROM public.ingredients
        WHERE is_active IS TRUE;

        IF v_active_count <> 10 THEN
            v_issues := array_append(v_issues, format('Exactly 10 active ingredients are required; found %s.', v_active_count));
        END IF;

        SELECT master_order INTO v_master_order
        FROM public.game_state
        WHERE id = 1;

        IF NOT public.admin_is_valid_ingredient_order(v_master_order) THEN
            v_issues := array_append(v_issues, 'Master order must contain 10 distinct active ingredients.');
        END IF;

        SELECT count(*),
               count(*) FILTER (WHERE witch_type = 'real'::public.witch_type),
               count(*) FILTER (WHERE witch_type = 'fake'::public.witch_type)
        INTO v_witch_count, v_real_count, v_fake_count
        FROM public.witches;

        IF v_witch_count <> 3 THEN
            v_issues := array_append(v_issues, format('Exactly 3 Witch identities are required; found %s.', v_witch_count));
        END IF;
        IF v_real_count <> 2 THEN
            v_issues := array_append(v_issues, format('Exactly 2 REAL Witches are required; found %s.', v_real_count));
        END IF;
        IF v_fake_count <> 1 THEN
            v_issues := array_append(v_issues, format('Exactly 1 FAKE Witch is required; found %s.', v_fake_count));
        END IF;

        SELECT EXISTS (
            SELECT 1
            FROM public.witches AS witch
            WHERE NOT public.admin_is_valid_ingredient_order(witch.display_order)
        )
        INTO v_invalid_witch_order;

        IF v_invalid_witch_order THEN
            v_issues := array_append(v_issues, 'Every Witch display order must contain 10 distinct active ingredients.');
        END IF;

        SELECT count(*), count(*) FILTER (WHERE organizer.is_active IS TRUE),
               count(DISTINCT organizer.witch_id) FILTER (WHERE organizer.is_active IS TRUE),
               COALESCE(bool_or(organizer.is_active IS TRUE AND (witch.id IS NULL OR organizer.witch_id IS NULL)), FALSE)
        INTO v_organizer_profile_count, v_active_organizer_count, v_distinct_active_witches, v_invalid_organizer_mapping
        FROM public.organizers AS organizer
        LEFT JOIN public.witches AS witch ON witch.id = organizer.witch_id;

        IF v_active_organizer_count <> 3 THEN
            v_issues := array_append(v_issues, format('Exactly 3 active organizer profiles are required; found %s.', v_active_organizer_count));
        END IF;
        IF v_organizer_profile_count <> 3 THEN
            v_issues := array_append(v_issues, format('Exactly 3 organizer profiles are required; found %s.', v_organizer_profile_count));
        END IF;
        IF v_distinct_active_witches <> 3 OR v_invalid_organizer_mapping THEN
            v_issues := array_append(v_issues, 'Each active organizer profile must map to a different Witch UUID.');
        END IF;

        RETURN v_issues;
    END;
    $$;


    -- Lifecycle transition RPC is obsolete: submissions are continuous and no admin
    -- start/pause/round transition is required. Drop the previously installed RPC.
    DROP FUNCTION IF EXISTS public.admin_transition_game(text);

    -- Configuration writes and submissions serialize on the singleton row.
    -- Configuration remains editable until a submission or winner is recorded.
    CREATE OR REPLACE FUNCTION public.admin_guard_game_configuration_write()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_witch_count integer;
        v_real_count integer;
    BEGIN
        PERFORM 1
        FROM public.game_state
        WHERE id = 1
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Game configuration singleton is unavailable'
                USING ERRCODE = 'P0001';
        END IF;
        IF EXISTS (SELECT 1 FROM public.attempts) OR EXISTS (SELECT 1 FROM public.winners) THEN
            RAISE EXCEPTION 'Game configuration is locked when game history exists'
                USING ERRCODE = 'P0001';
        END IF;

        IF TG_TABLE_NAME = 'ingredients' AND TG_OP = 'UPDATE' THEN
            IF OLD.is_active IS TRUE AND NEW.is_active IS FALSE THEN
                IF EXISTS (
                    SELECT 1
                    FROM public.game_state AS game
                    WHERE game.id = 1
                    AND OLD.id = ANY(COALESCE(game.master_order, ARRAY[]::uuid[]))
                ) OR EXISTS (
                    SELECT 1
                    FROM public.witches AS witch
                    WHERE OLD.id = ANY(COALESCE(witch.display_order, ARRAY[]::uuid[]))
                ) THEN
                    RAISE EXCEPTION 'Ingredient is referenced by a configured order'
                        USING ERRCODE = 'P0001';
                END IF;
            END IF;
        END IF;

        IF TG_TABLE_NAME = 'ingredients' AND TG_OP = 'DELETE' THEN
            IF EXISTS (
                SELECT 1
                FROM public.game_state AS game
                WHERE game.id = 1
                AND OLD.id = ANY(COALESCE(game.master_order, ARRAY[]::uuid[]))
            ) OR EXISTS (
                SELECT 1
                FROM public.witches AS witch
                WHERE OLD.id = ANY(COALESCE(witch.display_order, ARRAY[]::uuid[]))
            ) THEN
                RAISE EXCEPTION 'Ingredient is referenced by a configured order'
                    USING ERRCODE = 'P0001';
            END IF;
        END IF;

        IF TG_TABLE_NAME = 'game_state' THEN
            IF TG_OP = 'UPDATE' THEN
                IF NOT public.admin_is_valid_ingredient_order(NEW.master_order) THEN
                    RAISE EXCEPTION 'Master order must contain 10 distinct active ingredients'
                        USING ERRCODE = 'P0001';
                END IF;
            END IF;
        END IF;

        IF TG_TABLE_NAME = 'witches' AND TG_OP = 'INSERT' THEN
                IF NEW.display_order IS NOT NULL
                AND NOT public.admin_is_valid_ingredient_order(NEW.display_order) THEN
                    RAISE EXCEPTION 'Witch display order must contain 10 distinct active ingredients'
                        USING ERRCODE = 'P0001';
                END IF;
                SELECT count(*), count(*) FILTER (WHERE witch_type = 'real'::public.witch_type)
                INTO v_witch_count, v_real_count
                FROM public.witches;
                v_witch_count := v_witch_count + 1;
                IF NEW.witch_type = 'real'::public.witch_type THEN
                    v_real_count := v_real_count + 1;
                END IF;
                IF v_witch_count > 3 OR v_real_count > 2
                   OR (v_witch_count - v_real_count) > 1 THEN
                    RAISE EXCEPTION 'Three-Witch roster limit exceeded'
                        USING ERRCODE = 'P0001';
                END IF;
        ELSIF TG_TABLE_NAME = 'witches' AND TG_OP = 'DELETE' THEN
                IF EXISTS (SELECT 1 FROM public.organizers WHERE witch_id = OLD.id) THEN
                    RAISE EXCEPTION 'Witch is assigned to an organizer'
                        USING ERRCODE = 'P0001';
                END IF;
                IF EXISTS (SELECT 1 FROM public.attempts WHERE witch_id = OLD.id) THEN
                    RAISE EXCEPTION 'Witch has attempt history'
                        USING ERRCODE = 'P0001';
                END IF;
                IF EXISTS (SELECT 1 FROM public.winners WHERE witch_id = OLD.id) THEN
                    RAISE EXCEPTION 'Witch has winner history'
                        USING ERRCODE = 'P0001';
                END IF;
                SELECT count(*) INTO v_witch_count FROM public.witches;
                IF v_witch_count <= 3 THEN
                    RAISE EXCEPTION 'The three-Witch roster is fixed'
                        USING ERRCODE = 'P0001';
                END IF;
        ELSIF TG_TABLE_NAME = 'witches' AND TG_OP = 'UPDATE' THEN
                IF NEW.display_order IS NOT NULL
                AND NEW.display_order IS DISTINCT FROM OLD.display_order
                AND NOT public.admin_is_valid_ingredient_order(NEW.display_order) THEN
                    RAISE EXCEPTION 'Witch display order must contain 10 distinct active ingredients'
                        USING ERRCODE = 'P0001';
                END IF;
                SELECT count(*), count(*) FILTER (WHERE witch_type = 'real'::public.witch_type)
                INTO v_witch_count, v_real_count
                FROM public.witches
                WHERE id <> OLD.id;
                v_witch_count := v_witch_count + 1;
                IF NEW.witch_type = 'real'::public.witch_type THEN
                    v_real_count := v_real_count + 1;
                END IF;
                IF v_witch_count > 3 OR v_real_count > 2
                   OR (v_witch_count - v_real_count) > 1 THEN
                    RAISE EXCEPTION 'Three-Witch roster limit exceeded'
                        USING ERRCODE = 'P0001';
                END IF;
        END IF;

        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END;
    $$;

    DROP TRIGGER IF EXISTS admin_guard_ingredient_configuration ON public.ingredients;
    CREATE TRIGGER admin_guard_ingredient_configuration
    BEFORE INSERT OR UPDATE OR DELETE ON public.ingredients
    FOR EACH ROW EXECUTE FUNCTION public.admin_guard_game_configuration_write();

    DROP TRIGGER IF EXISTS admin_guard_witch_configuration ON public.witches;
    CREATE TRIGGER admin_guard_witch_configuration
    BEFORE INSERT OR UPDATE OR DELETE ON public.witches
    FOR EACH ROW EXECUTE FUNCTION public.admin_guard_game_configuration_write();

    DROP TRIGGER IF EXISTS admin_guard_master_order_configuration ON public.game_state;
    CREATE TRIGGER admin_guard_master_order_configuration
    BEFORE UPDATE OF master_order ON public.game_state
    FOR EACH ROW EXECUTE FUNCTION public.admin_guard_game_configuration_write();

    -- Team activation changes take the same lock as submissions so an active-team
    -- check cannot race with deactivation. No team_game_state row is initialized;
    -- accepted attempts are the authoritative source for chance usage.
    CREATE OR REPLACE FUNCTION public.admin_serialize_team_gameplay_change()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    BEGIN
        PERFORM 1
        FROM public.game_state
        WHERE id = 1
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Game configuration singleton is unavailable'
                USING ERRCODE = 'P0001';
        END IF;
        RETURN NEW;
    END;
    $$;

    DROP TRIGGER IF EXISTS admin_ensure_active_team_game_state ON public.teams;
    DROP FUNCTION IF EXISTS public.admin_ensure_active_team_game_state();
    DROP TRIGGER IF EXISTS admin_serialize_team_gameplay_change ON public.teams;
    CREATE TRIGGER admin_serialize_team_gameplay_change
    AFTER INSERT OR UPDATE OF is_active ON public.teams
    FOR EACH ROW EXECUTE FUNCTION public.admin_serialize_team_gameplay_change();

-- Active Supabase Auth profiles map one-to-one to the three Witch identities.
-- This trigger never creates physical-operator rows; the shared fake account is
-- represented by one organizer profile assigned to the single fake Witch UUID.
CREATE OR REPLACE FUNCTION public.admin_guard_organizer_witch_assignment()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_active_count integer;
    v_profile_count integer;
BEGIN
    PERFORM 1 FROM public.game_state WHERE id = 1 FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Game configuration singleton is unavailable'
            USING ERRCODE = 'P0001';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;

    IF TG_OP = 'INSERT' THEN
        SELECT count(*) INTO v_profile_count FROM public.organizers;
        IF v_profile_count >= 3 THEN
            RAISE EXCEPTION 'Exactly three organizer profiles are supported'
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    IF NEW.is_active IS TRUE THEN
        IF NEW.witch_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.witches WHERE id = NEW.witch_id
        ) THEN
            RAISE EXCEPTION 'Active organizer must be assigned to an existing Witch UUID'
                USING ERRCODE = 'P0001';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.organizers AS other
            WHERE other.id <> NEW.id
              AND other.is_active IS TRUE
              AND other.witch_id = NEW.witch_id
        ) THEN
            RAISE EXCEPTION 'A Witch UUID may be assigned to only one active organizer'
                USING ERRCODE = 'P0001';
        END IF;

        SELECT count(*) INTO v_active_count
        FROM public.organizers AS other
        WHERE other.is_active IS TRUE
          AND other.id <> NEW.id;
        IF v_active_count >= 3 THEN
            RAISE EXCEPTION 'Exactly three active organizer profiles are supported'
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS admin_guard_organizer_witch_assignment ON public.organizers;
CREATE TRIGGER admin_guard_organizer_witch_assignment
BEFORE INSERT OR UPDATE OR DELETE ON public.organizers
FOR EACH ROW EXECUTE FUNCTION public.admin_guard_organizer_witch_assignment();

    REVOKE ALL ON FUNCTION public.admin_game_configuration_issues() FROM PUBLIC, anon, authenticated;
    GRANT EXECUTE ON FUNCTION public.admin_game_configuration_issues() TO service_role;

    REVOKE ALL ON FUNCTION public.admin_is_valid_ingredient_order(uuid[]) FROM PUBLIC, anon, authenticated;
    GRANT EXECUTE ON FUNCTION public.admin_is_valid_ingredient_order(uuid[]) TO service_role;

    REVOKE ALL ON FUNCTION public.admin_guard_game_configuration_write() FROM PUBLIC, anon, authenticated;
    REVOKE ALL ON FUNCTION public.admin_serialize_team_gameplay_change() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.admin_guard_organizer_witch_assignment() FROM PUBLIC, anon, authenticated;
