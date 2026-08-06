from enum import StrEnum


class ProfileState(StrEnum):
    REQUESTED = "REQUESTED"
    OWNER_AUTHORIZING = "OWNER_AUTHORIZING"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    CHALLENGE_REQUIRED = "CHALLENGE_REQUIRED"
    EXPIRED = "EXPIRED"
    QUARANTINED = "QUARANTINED"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"
    PURGED = "PURGED"


PROFILE_TRANSITIONS: dict[ProfileState, frozenset[ProfileState]] = {
    ProfileState.REQUESTED: frozenset({ProfileState.OWNER_AUTHORIZING, ProfileState.REVOKED}),
    ProfileState.OWNER_AUTHORIZING: frozenset(
        {ProfileState.ACTIVE, ProfileState.EXPIRED, ProfileState.REVOKED}
    ),
    ProfileState.ACTIVE: frozenset(
        {
            ProfileState.DEGRADED,
            ProfileState.CHALLENGE_REQUIRED,
            ProfileState.EXPIRED,
            ProfileState.QUARANTINED,
            ProfileState.SUPERSEDED,
            ProfileState.REVOKED,
        }
    ),
    ProfileState.DEGRADED: frozenset(
        {
            ProfileState.ACTIVE,
            ProfileState.CHALLENGE_REQUIRED,
            ProfileState.QUARANTINED,
            ProfileState.REVOKED,
        }
    ),
    ProfileState.CHALLENGE_REQUIRED: frozenset(
        {ProfileState.ACTIVE, ProfileState.EXPIRED, ProfileState.QUARANTINED, ProfileState.REVOKED}
    ),
    ProfileState.EXPIRED: frozenset({ProfileState.OWNER_AUTHORIZING, ProfileState.REVOKED}),
    ProfileState.QUARANTINED: frozenset({ProfileState.OWNER_AUTHORIZING, ProfileState.REVOKED}),
    ProfileState.SUPERSEDED: frozenset({ProfileState.REVOKED}),
    ProfileState.REVOKED: frozenset({ProfileState.PURGED}),
    ProfileState.PURGED: frozenset(),
}


def transition(current: ProfileState, target: ProfileState) -> ProfileState:
    if target not in PROFILE_TRANSITIONS[current]:
        raise ValueError(f"invalid profile transition: {current}->{target}")
    return target
