from pwdlib import PasswordHash

_team_code_hasher = PasswordHash.recommended()


def hash_credential(credential: str) -> str:
    """Hash an application credential value using the established Argon2 scheme."""
    return _team_code_hasher.hash(credential)


def hash_team_code(team_code: str) -> str:
    """Hash a team access code for storage in teams.team_code_hash."""
    return hash_credential(team_code)


def verify_team_code(team_code: str, team_code_hash: str) -> bool:
    """Verify a team access code against its stored hash."""
    try:
        return _team_code_hasher.verify(team_code, team_code_hash)
    except Exception:
        return False
