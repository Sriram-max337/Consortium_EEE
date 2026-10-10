-- Organizer management requires a durable username with database-enforced
-- case-insensitive uniqueness. This is additive and does not change Auth IDs,
-- Witch assignments, or game state.
ALTER TABLE public.organizers
    ADD COLUMN IF NOT EXISTS username text;

-- Recover usernames already held in server-managed Auth metadata, where present.
UPDATE public.organizers AS organizer
SET username = NULLIF(btrim(auth_user.raw_app_meta_data ->> 'username'), '')
FROM auth.users AS auth_user
WHERE auth_user.id = organizer.id
  AND organizer.username IS NULL
  AND NULLIF(btrim(auth_user.raw_app_meta_data ->> 'username'), '') IS NOT NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.organizers
        WHERE username IS NOT NULL
        GROUP BY lower(btrim(username))
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Resolve duplicate organizer usernames before applying the unique index';
    END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS organizers_username_case_insensitive_key
    ON public.organizers (lower(btrim(username)))
    WHERE username IS NOT NULL;
