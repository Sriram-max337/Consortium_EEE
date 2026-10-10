-- Witch Hunt schema hardening for exact-position feedback and two
-- team-wide submissions. Apply in Supabase SQL Editor after
-- admin_game_lifecycle_rpcs.sql and before witch_hunt_engine.sql.
-- This migration preserves all rows: it does not reset attempts, winners, or
-- team_game_state. It discovers existing constraint names from pg_catalog.

BEGIN;

DO $$
DECLARE
    v_constraint record;
    v_definition text;
    v_has_team_attempt_unique boolean;
    v_has_team_witch_unique boolean;
BEGIN
    IF to_regclass('public.team_game_state') IS NULL
       OR to_regclass('public.attempts') IS NULL THEN
        RAISE EXCEPTION 'Expected public.team_game_state and public.attempts tables';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.team_game_state WHERE attempts_used < 0 OR attempts_used > 2
    ) THEN
        RAISE EXCEPTION 'team_game_state contains attempts_used outside 0..2; review rows before applying';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.attempts WHERE attempt_number < 1 OR attempt_number > 2
    ) THEN
        RAISE EXCEPTION 'attempts contains attempt_number outside 1..2; review rows before applying';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.attempts
        GROUP BY team_id, attempt_number
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'attempts has duplicate (team_id, attempt_number); review rows before applying';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.attempts
        GROUP BY team_id, witch_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'attempts has duplicate (team_id, witch_id); review rows before applying';
    END IF;

    -- Replace only checks that explicitly permit more than two. Other checks
    -- (for example the non-negative lower bound) are retained.
    FOR v_constraint IN
        SELECT c.conname, pg_get_constraintdef(c.oid) AS definition
        FROM pg_constraint c
        WHERE c.conrelid = 'public.team_game_state'::regclass
          AND c.contype = 'c'
    LOOP
        v_definition := v_constraint.definition;
        IF v_definition ILIKE '%attempts_used%'
           AND v_definition ~* '(<=\s*3|<\s*4|between\s*0\s*and\s*3|in\s*\(\s*0\s*,\s*1\s*,\s*2\s*,\s*3\s*\))' THEN
            EXECUTE format('ALTER TABLE public.team_game_state DROP CONSTRAINT %I', v_constraint.conname);
        END IF;
    END LOOP;

    -- Do not silently leave an unfamiliar constraint that may alter the
    -- two-chance cap. A simple non-negative check is safe to retain.
    FOR v_constraint IN
        SELECT c.conname, pg_get_constraintdef(c.oid) AS definition
        FROM pg_constraint c
        WHERE c.conrelid = 'public.team_game_state'::regclass
          AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) ILIKE '%attempts_used%'
    LOOP
        v_definition := regexp_replace(lower(v_constraint.definition), '[[:space:]()]', '', 'g');
        IF v_definition NOT IN (
            'checkattempts_used>=0',
            'checkattempts_used<=2',
            'checkattempts_usedbetween0and2',
            'checkattempts_used>=0andattempts_used<=2',
            'checkattempts_usedin0,1,2'
        ) THEN
            RAISE EXCEPTION 'Unrecognized team_game_state attempts_used constraint % (%); inspect it before changing the attempt cap',
                v_constraint.conname, v_constraint.definition;
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.team_game_state'::regclass
          AND conname = 'team_game_state_attempts_used_0_2_check'
    ) THEN
        ALTER TABLE public.team_game_state
            ADD CONSTRAINT team_game_state_attempts_used_0_2_check
            CHECK (attempts_used BETWEEN 0 AND 2);
    END IF;

    FOR v_constraint IN
        SELECT c.conname, pg_get_constraintdef(c.oid) AS definition
        FROM pg_constraint c
        WHERE c.conrelid = 'public.attempts'::regclass
          AND c.contype = 'c'
    LOOP
        v_definition := v_constraint.definition;
        IF v_definition ILIKE '%attempt_number%'
           AND v_definition ~* '(<=\s*3|<\s*4|between\s*1\s*and\s*3|in\s*\(\s*1\s*,\s*2\s*,\s*3\s*\))' THEN
            EXECUTE format('ALTER TABLE public.attempts DROP CONSTRAINT %I', v_constraint.conname);
        END IF;
    END LOOP;

    FOR v_constraint IN
        SELECT c.conname, pg_get_constraintdef(c.oid) AS definition
        FROM pg_constraint c
        WHERE c.conrelid = 'public.attempts'::regclass
          AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) ILIKE '%attempt_number%'
    LOOP
        v_definition := regexp_replace(lower(v_constraint.definition), '[[:space:]()]', '', 'g');
        IF v_definition NOT IN (
            'checkattempt_number>=1',
            'checkattempt_number<=2',
            'checkattempt_numberbetween1and2',
            'checkattempt_number>=1andattempt_number<=2',
            'checkattempt_numberin1,2'
        ) THEN
            RAISE EXCEPTION 'Unrecognized attempts attempt_number constraint % (%); inspect it before changing the attempt cap',
                v_constraint.conname, v_constraint.definition;
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.attempts'::regclass
          AND conname = 'attempts_attempt_number_1_2_check'
    ) THEN
        ALTER TABLE public.attempts
            ADD CONSTRAINT attempts_attempt_number_1_2_check
            CHECK (attempt_number BETWEEN 1 AND 2);
    END IF;

    -- Team attempt numbers are global across all Witches. Keep any
    -- existing unique constraints, and add the exact invariant if absent.
    SELECT EXISTS (
        SELECT 1
        FROM pg_constraint c
        WHERE c.conrelid = 'public.attempts'::regclass
          AND c.contype = 'u'
          AND (
              SELECT array_agg(a.attname::text ORDER BY a.attname::text)
              FROM unnest(c.conkey) AS key(attnum)
              JOIN pg_attribute a
                ON a.attrelid = c.conrelid AND a.attnum = key.attnum
          ) = ARRAY['attempt_number', 'team_id']::text[]
    ) INTO v_has_team_attempt_unique;

    IF NOT v_has_team_attempt_unique THEN
        ALTER TABLE public.attempts
            ADD CONSTRAINT attempts_team_attempt_number_key
            UNIQUE (team_id, attempt_number);
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM pg_constraint c
        WHERE c.conrelid = 'public.attempts'::regclass
          AND c.contype = 'u'
          AND (
              SELECT array_agg(a.attname::text ORDER BY a.attname::text)
              FROM unnest(c.conkey) AS key(attnum)
              JOIN pg_attribute a
                ON a.attrelid = c.conrelid AND a.attnum = key.attnum
          ) = ARRAY['team_id', 'witch_id']::text[]
    ) INTO v_has_team_witch_unique;

    IF NOT v_has_team_witch_unique THEN
        ALTER TABLE public.attempts
            ADD CONSTRAINT attempts_team_witch_key
            UNIQUE (team_id, witch_id);
    END IF;
END;
$$;

ALTER TABLE public.attempts
    ADD COLUMN IF NOT EXISTS match_percentage smallint;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.attempts'::regclass
          AND conname = 'attempts_match_percentage_tens_check'
    ) THEN
        ALTER TABLE public.attempts
            ADD CONSTRAINT attempts_match_percentage_tens_check
            CHECK (
                match_percentage IS NULL OR
                match_percentage IN (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
            );
    END IF;
END;
$$;

COMMENT ON COLUMN public.attempts.match_percentage IS
    'Server-calculated exact-position match percentage; NULL only for attempts recorded before this migration.';

COMMIT;
