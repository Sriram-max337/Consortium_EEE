-- Atomic organizer gameplay submissions and authoritative team state refresh.
-- Depends on admin_is_valid_ingredient_order(uuid[]) from
-- admin_game_lifecycle_rpcs.sql and match_percentage from
-- witch_hunt_engine_v2_schema.sql. This file adds functions only; it changes
-- no tables, enums, RLS policies, or Supabase Auth configuration.
--witch_hunt_engine.sql
DO $$
BEGIN
    IF to_regprocedure('public.admin_is_valid_ingredient_order(uuid[])') IS NULL THEN
        RAISE EXCEPTION 'Apply admin_game_lifecycle_rpcs.sql before witch_hunt_engine.sql';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.organizer_submit_attempt(
    p_organizer_id uuid,
    p_team_id text,
    p_submitted_order uuid[]
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_game public.game_state%ROWTYPE;
    v_witch public.witches%ROWTYPE;
    v_witch_id uuid;
    v_attempt_number integer;
    v_attempts_remaining integer;
    v_match_count integer;
    v_match_percentage integer;
    v_result public.attempt_result;
    v_winner_1_team_id text;
    v_winner_count integer;
    v_qualified boolean := false;
    v_now timestamptz;
BEGIN
    -- Serialize submissions, configuration edits, and team activation changes.
    SELECT * INTO v_game
    FROM public.game_state
    WHERE id = 1
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('ok', false, 'code', 'submission_unavailable');
    END IF;

    SELECT witch_id INTO v_witch_id
    FROM public.organizers
    WHERE id = p_organizer_id
      AND is_active IS TRUE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('ok', false, 'code', 'organizer_unavailable');
    END IF;

    SELECT * INTO v_witch
    FROM public.witches
    WHERE id = v_witch_id;
    IF NOT FOUND OR v_witch.is_locked IS TRUE THEN
        RETURN jsonb_build_object('ok', false, 'code', 'witch_unavailable');
    END IF;

    IF NOT public.admin_is_valid_ingredient_order(p_submitted_order) THEN
        RETURN jsonb_build_object('ok', false, 'code', 'invalid_order');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.teams
        WHERE team_id = p_team_id
          AND is_active IS TRUE
    ) THEN
        RETURN jsonb_build_object('ok', false, 'code', 'team_unavailable');
    END IF;

    -- The removed lifecycle start used to enforce setup readiness. Keep that
    -- configuration gate at submission time without requiring a game status.
    IF cardinality(public.admin_game_configuration_issues()) > 0 THEN
        RETURN jsonb_build_object('ok', false, 'code', 'configuration_not_ready');
    END IF;

    -- Existing winner positions remain ordered by first eligible correct
    -- submissions; there is no longer a manual round transition.
    SELECT team_id INTO v_winner_1_team_id
    FROM public.winners
    WHERE position = 1;
    SELECT count(*)::integer INTO v_winner_count FROM public.winners;
    IF v_winner_count >= 2 THEN
        RETURN jsonb_build_object('ok', false, 'code', 'winners_complete');
    END IF;
    IF v_winner_1_team_id = p_team_id THEN
        RETURN jsonb_build_object('ok', false, 'code', 'winner_one_excluded');
    END IF;

    -- Attempts are authoritative. The singleton row lock serializes this
    -- count with the later insert, even when no team_game_state row exists.
    SELECT count(*)::integer INTO v_attempt_number
    FROM public.attempts
    WHERE team_id = p_team_id;

    -- Enforce one submission per team at each Witch before consuming a chance
    -- or writing any success/audit rows. The unique constraint on
    -- attempts(team_id, witch_id) is the final safeguard for concurrent calls.
    IF EXISTS (
        SELECT 1
        FROM public.attempts
        WHERE team_id = p_team_id
          AND witch_id = v_witch.id
    ) THEN
        RETURN jsonb_build_object('ok', false, 'code', 'duplicate_witch_attempt');
    END IF;

    IF v_attempt_number >= 2 THEN
        RETURN jsonb_build_object('ok', false, 'code', 'attempts_exhausted');
    END IF;

    v_now := clock_timestamp();
    v_attempt_number := v_attempt_number + 1;
    v_attempts_remaining := 2 - v_attempt_number;

    SELECT count(*)::integer INTO v_match_count
    FROM generate_subscripts(v_game.master_order, 1) AS positions(i)
    WHERE v_game.master_order[i] = p_submitted_order[i];
    v_match_percentage := v_match_count * 10;

    -- The global master order is the answer key. Witch display_order remains
    -- separate presentation/configuration data and is never returned here.
    IF v_witch.witch_type = 'fake'::public.witch_type THEN
        v_result := 'fake_witch'::public.attempt_result;
    ELSIF p_submitted_order IS DISTINCT FROM v_game.master_order THEN
        v_result := 'wrong_order'::public.attempt_result;
    ELSIF v_winner_1_team_id IS NULL THEN
        v_result := 'winner_1'::public.attempt_result;
    ELSE
        v_result := 'winner_2'::public.attempt_result;
    END IF;

    v_qualified := v_result IN (
        'winner_1'::public.attempt_result,
        'winner_2'::public.attempt_result
    );

    -- Keep legacy counters as a mirror only; never use them to enforce limits.
    INSERT INTO public.team_game_state (
        team_id, attempts_used, is_locked, qualified, created_at, updated_at
    )
    VALUES (
        p_team_id, v_attempt_number, (v_attempts_remaining = 0 OR v_qualified),
        v_qualified, v_now, v_now
    )
    ON CONFLICT (team_id) DO UPDATE
    SET attempts_used = EXCLUDED.attempts_used,
        is_locked = public.team_game_state.is_locked OR EXCLUDED.is_locked,
        qualified = public.team_game_state.qualified OR EXCLUDED.qualified,
        updated_at = EXCLUDED.updated_at;

    INSERT INTO public.attempts (
        team_id, witch_id, attempt_number, submitted_order, result,
        match_percentage, created_at
    )
    VALUES (
        p_team_id, v_witch.id, v_attempt_number, p_submitted_order, v_result,
        v_match_percentage, v_now
    );

    INSERT INTO public.activity_logs (team_id, witch_id, event_type, metadata, created_at)
    VALUES (
        p_team_id,
        v_witch.id,
        'attempt_submitted',
        jsonb_build_object(
            'attempt_number', v_attempt_number,
            'result', v_result::text,
            'match_percentage', v_match_percentage
        ),
        v_now
    );

    IF v_result = 'winner_1'::public.attempt_result THEN
        INSERT INTO public.winners (position, team_id, witch_id, won_at)
        VALUES (1, p_team_id, v_witch.id, v_now);

        INSERT INTO public.activity_logs (team_id, witch_id, event_type, metadata, created_at)
        VALUES (
            p_team_id, v_witch.id, 'winner_recorded',
            jsonb_build_object('position', 1),
            v_now
        );
    ELSIF v_result = 'winner_2'::public.attempt_result THEN
        INSERT INTO public.winners (position, team_id, witch_id, won_at)
        VALUES (2, p_team_id, v_witch.id, v_now);

        INSERT INTO public.activity_logs (team_id, witch_id, event_type, metadata, created_at)
        VALUES (
            p_team_id, v_witch.id, 'winner_recorded',
            jsonb_build_object('position', 2),
            v_now
        );
    END IF;

    -- Witch type and this submission result are intentionally visible only in
    -- the successful response. No other Witch configuration is returned.
    RETURN jsonb_build_object(
        'ok', true,
        'match_percentage', v_match_percentage,
        'result', CASE
            WHEN v_result = 'winner_2'::public.attempt_result THEN 'runner_up'
            ELSE v_result::text
        END,
        'witch_type', CASE
            WHEN v_witch.witch_type = 'real'::public.witch_type THEN 'REAL'
            ELSE 'FAKE'
        END,
        'attempts_used', v_attempt_number,
        'attempts_remaining', v_attempts_remaining
    );
END;
$$;

REVOKE ALL ON FUNCTION public.organizer_submit_attempt(uuid, text, uuid[])
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.organizer_submit_attempt(uuid, text, uuid[])
    TO service_role;

-- Chance usage is derived only from accepted attempts. No lifecycle or
-- team_game_state initialization is required to select a registered team.
CREATE OR REPLACE FUNCTION public.organizer_get_team_submission_state(
    p_organizer_id uuid,
    p_team_id text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_witch_id uuid;
    v_attempts_used integer;
    v_has_submitted boolean;
BEGIN
    SELECT witch_id INTO v_witch_id
    FROM public.organizers
    WHERE id = p_organizer_id
      AND is_active IS TRUE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('ok', false, 'code', 'organizer_unavailable');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.witches WHERE id = v_witch_id
    ) THEN
        RETURN jsonb_build_object('ok', false, 'code', 'organizer_unavailable');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.teams
        WHERE team_id = p_team_id
          AND is_active IS TRUE
    ) THEN
        RETURN jsonb_build_object('ok', false, 'code', 'team_unavailable');
    END IF;

    SELECT count(*)::integer INTO v_attempts_used
    FROM public.attempts
    WHERE team_id = p_team_id;

    SELECT EXISTS (
        SELECT 1
        FROM public.attempts
        WHERE team_id = p_team_id
          AND witch_id = v_witch_id
    ) INTO v_has_submitted;

    RETURN jsonb_build_object(
        'ok', true,
        'team_id', p_team_id,
        'attempts_used', v_attempts_used,
        'attempts_remaining', greatest(0, 2 - v_attempts_used),
        'has_submitted_at_assigned_witch', v_has_submitted
    );
END;
$$;

REVOKE ALL ON FUNCTION public.organizer_get_team_submission_state(uuid, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.organizer_get_team_submission_state(uuid, text)
    TO service_role;

NOTIFY pgrst, 'reload schema';
