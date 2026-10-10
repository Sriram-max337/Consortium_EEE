-- Atomic admin team/member writes. This adds functions only; it does not alter tables.
-- Execute through Supabase SQL Editor before using POST/PATCH /admin/teams.

CREATE OR REPLACE FUNCTION public.admin_create_team_with_members(
    p_team jsonb,
    p_members jsonb
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    member_data jsonb;
BEGIN
    IF p_members IS NULL OR jsonb_typeof(p_members) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'invalid team member list' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(p_members) > 3 THEN
        RAISE EXCEPTION 'invalid team member list' USING ERRCODE = '22023';
    END IF;

    INSERT INTO public.teams (
        team_id,
        team_name,
        college_name,
        department,
        year,
        email,
        phone,
        emergency_contact,
        team_code_hash,
        is_active,
        created_at,
        updated_at
    ) VALUES (
        p_team->>'team_id',
        p_team->>'team_name',
        p_team->>'college_name',
        p_team->>'department',
        p_team->>'year',
        p_team->>'email',
        p_team->>'phone',
        p_team->>'emergency_contact',
        p_team->>'team_code_hash',
        TRUE,
        NOW(),
        NOW()
    );

    FOR member_data IN
        SELECT value FROM jsonb_array_elements(p_members) AS member_values(value)
    LOOP
        INSERT INTO public.team_members (id, team_id, member_number, name, roll_number, created_at)
        VALUES (
            gen_random_uuid(),
            p_team->>'team_id',
            (member_data->>'member_number')::smallint,
            member_data->>'name',
            member_data->>'roll_number',
            NOW()
        );
    END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION public.admin_create_team_with_members(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_create_team_with_members(jsonb, jsonb) TO service_role;


CREATE OR REPLACE FUNCTION public.admin_patch_team_with_members(
    p_team_id text,
    p_changes jsonb,
    p_replace_members boolean,
    p_members jsonb
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
    member_data jsonb;
BEGIN
    IF p_replace_members THEN
        IF p_members IS NULL OR jsonb_typeof(p_members) IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'invalid team member list' USING ERRCODE = '22023';
        END IF;
        IF jsonb_array_length(p_members) > 3 THEN
            RAISE EXCEPTION 'invalid team member list' USING ERRCODE = '22023';
        END IF;
    END IF;

    UPDATE public.teams
    SET
        team_name = CASE WHEN p_changes ? 'team_name' THEN p_changes->>'team_name' ELSE team_name END,
        college_name = CASE WHEN p_changes ? 'college_name' THEN p_changes->>'college_name' ELSE college_name END,
        department = CASE WHEN p_changes ? 'department' THEN p_changes->>'department' ELSE department END,
        year = CASE WHEN p_changes ? 'year' THEN p_changes->>'year' ELSE year END,
        email = CASE WHEN p_changes ? 'email' THEN p_changes->>'email' ELSE email END,
        phone = CASE WHEN p_changes ? 'phone' THEN p_changes->>'phone' ELSE phone END,
        emergency_contact = CASE WHEN p_changes ? 'emergency_contact' THEN p_changes->>'emergency_contact' ELSE emergency_contact END,
        updated_at = NOW()
    WHERE team_id = p_team_id;

    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;

    IF p_replace_members THEN
        DELETE FROM public.team_members WHERE team_id = p_team_id;
        FOR member_data IN
            SELECT value FROM jsonb_array_elements(p_members) AS member_values(value)
        LOOP
            INSERT INTO public.team_members (id, team_id, member_number, name, roll_number, created_at)
            VALUES (
                gen_random_uuid(),
                p_team_id,
                (member_data->>'member_number')::smallint,
                member_data->>'name',
                member_data->>'roll_number',
                NOW()
            );
        END LOOP;
    END IF;

    RETURN TRUE;
END;
$$;

REVOKE ALL ON FUNCTION public.admin_patch_team_with_members(text, jsonb, boolean, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_patch_team_with_members(text, jsonb, boolean, jsonb) TO service_role;
