-- Switch Witch Hunt gameplay to a stateless binary ingredient-order checker.
-- This migration preserves all tables and rows. It retires the old attempt RPCs,
-- removes team-gameplay serialization, and keeps ingredient/master-order validation
-- for the admin configuration UI. Legacy Witch/organizer admin triggers remain.

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
    v_master_order uuid[];
BEGIN
    SELECT count(*) INTO v_active_count
    FROM public.ingredients
    WHERE is_active IS TRUE;

    IF v_active_count <> 10 THEN
        v_issues := array_append(v_issues,
            format('Exactly 10 active ingredients are required; found %s.', v_active_count));
    END IF;

    SELECT master_order INTO v_master_order
    FROM public.game_state
    WHERE id = 1;

    IF NOT public.admin_is_valid_ingredient_order(v_master_order) THEN
        v_issues := array_append(v_issues,
            'Master order must contain each of the 10 active ingredients exactly once.');
    END IF;

    RETURN v_issues;
END;
$$;

CREATE OR REPLACE FUNCTION public.admin_guard_checker_configuration_write()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM 1 FROM public.game_state WHERE id = 1 FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Game configuration singleton is unavailable'
            USING ERRCODE = 'P0001';
    END IF;

    IF TG_TABLE_NAME = 'ingredients' THEN
        IF TG_OP = 'UPDATE' THEN
            IF OLD.is_active IS TRUE AND NEW.is_active IS FALSE THEN
                IF EXISTS (
                    SELECT 1 FROM public.game_state AS game
                    WHERE game.id = 1
                      AND OLD.id = ANY(COALESCE(game.master_order, ARRAY[]::uuid[]))
                ) THEN
                    RAISE EXCEPTION 'Ingredient is referenced by the master order'
                        USING ERRCODE = 'P0001';
                END IF;
            END IF;
        ELSIF TG_OP = 'DELETE' THEN
            IF EXISTS (
                SELECT 1 FROM public.game_state AS game
                WHERE game.id = 1
                  AND OLD.id = ANY(COALESCE(game.master_order, ARRAY[]::uuid[]))
            ) THEN
                RAISE EXCEPTION 'Ingredient is referenced by the master order'
                    USING ERRCODE = 'P0001';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME = 'game_state' THEN
        IF TG_OP = 'UPDATE' THEN
            IF NOT public.admin_is_valid_ingredient_order(NEW.master_order) THEN
                RAISE EXCEPTION 'Master order must contain 10 distinct active ingredients'
                    USING ERRCODE = 'P0001';
            END IF;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

-- The existing Witch and organizer administration triggers remain available for
-- legacy admin endpoints, but neither is part of checker authorization/readiness.
DROP TRIGGER IF EXISTS admin_guard_ingredient_configuration ON public.ingredients;
CREATE TRIGGER admin_guard_ingredient_configuration
BEFORE INSERT OR UPDATE OR DELETE ON public.ingredients
FOR EACH ROW EXECUTE FUNCTION public.admin_guard_checker_configuration_write();

DROP TRIGGER IF EXISTS admin_guard_master_order_configuration ON public.game_state;
CREATE TRIGGER admin_guard_master_order_configuration
BEFORE UPDATE OF master_order ON public.game_state
FOR EACH ROW EXECUTE FUNCTION public.admin_guard_checker_configuration_write();

-- Team activation no longer serializes with an attempt engine.
DROP TRIGGER IF EXISTS admin_serialize_team_gameplay_change ON public.teams;
DROP TRIGGER IF EXISTS admin_ensure_active_team_game_state ON public.teams;
DROP FUNCTION IF EXISTS public.admin_serialize_team_gameplay_change();
DROP FUNCTION IF EXISTS public.admin_ensure_active_team_game_state();
DROP FUNCTION IF EXISTS public.admin_transition_game(text);

-- Retire the only RPCs that write legacy attempt/winner/team-state/activity data.
DROP FUNCTION IF EXISTS public.organizer_submit_attempt(uuid, text, uuid[]);
DROP FUNCTION IF EXISTS public.organizer_get_team_submission_state(uuid, text);

REVOKE ALL ON FUNCTION public.admin_game_configuration_issues() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_game_configuration_issues() TO service_role;
REVOKE ALL ON FUNCTION public.admin_is_valid_ingredient_order(uuid[]) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_is_valid_ingredient_order(uuid[]) TO service_role;
REVOKE ALL ON FUNCTION public.admin_guard_checker_configuration_write() FROM PUBLIC, anon, authenticated;

NOTIFY pgrst, 'reload schema';
