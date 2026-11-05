"""Who may do what — decided on the server, from the run, not from the environment.

Release 2.1, R4. The application already had accounts and a coarse `role` field, and the
documentation was honest about what was missing: no role check on operations. An author could
draft a contract, approve it and publish it, which makes the approval gate a formality rather
than a control.

Two application roles, and one rule that does most of the work:

**The author cannot approve their own run.** This is not about trust. A reviewer who did not
write the document reads it differently, and the whole value of the gate is that second reading.
An administrator is not exempt either — an admin may approve, but only when appointed reviewer of
that run, because "administrator" describes how someone got an account, not what they agreed to.

Roles come from the account; who is the reviewer comes from the **run**. A rule based on an
environment variable would be a rule about the deployment, and the question here is about this
contract and this person.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AppRole(str, Enum):
    """The two application roles, plus the two that are not really roles."""

    AUTHOR = "author"
    APPROVER = "approver"
    #: Administers the deployment. Does not, by itself, authorise approving a contract.
    ADMIN = "admin"
    #: A machine account. Acts on behalf of whoever configured it, and never signs in.
    SERVICE = "service"


class Permission(str, Enum):
    DRAFT = "draft"
    REVISE = "revise"
    APPROVE = "approve"
    REJECT = "reject"
    EXPORT = "export"
    VIEW = "view"


#: What each role may do *in principle*. Per-run rules narrow this further; nothing here grants
#: approval of a specific contract.
ROLE_PERMISSIONS: dict[AppRole, frozenset[Permission]] = {
    AppRole.AUTHOR: frozenset(
        {Permission.DRAFT, Permission.REVISE, Permission.EXPORT, Permission.VIEW}
    ),
    AppRole.APPROVER: frozenset(
        {Permission.APPROVE, Permission.REJECT, Permission.EXPORT, Permission.VIEW}
    ),
    AppRole.ADMIN: frozenset(
        {
            Permission.DRAFT,
            Permission.REVISE,
            Permission.APPROVE,
            Permission.REJECT,
            Permission.EXPORT,
            Permission.VIEW,
        }
    ),
    AppRole.SERVICE: frozenset(
        {Permission.DRAFT, Permission.REVISE, Permission.EXPORT, Permission.VIEW}
    ),
}


class DenialCode(str, Enum):
    """Why an action was refused. Stable strings, shown to the person refused."""

    NOT_A_PARTICIPANT = "not_a_participant"
    AUTHOR_CANNOT_APPROVE = "author_cannot_approve"
    NOT_THE_REVIEWER = "not_the_reviewer"
    ADMIN_NOT_APPOINTED = "admin_not_appointed"
    ROLE_MAY_NOT_APPROVE = "role_may_not_approve"
    ROLE_MAY_NOT_DRAFT = "role_may_not_draft"


REASONS: dict[DenialCode, str] = {
    DenialCode.NOT_A_PARTICIPANT: (
        "You are not a participant of this run. Ask the author to add you as its reviewer."
    ),
    DenialCode.AUTHOR_CANNOT_APPROVE: (
        "The author of a contract cannot approve it. A second person has to review this version."
    ),
    DenialCode.NOT_THE_REVIEWER: (
        "This run has a different reviewer. Only the appointed reviewer can approve it."
    ),
    DenialCode.ADMIN_NOT_APPOINTED: (
        "An administrator cannot approve a contract they are not the reviewer of. Being an "
        "administrator says how you got an account, not what you agreed to."
    ),
    DenialCode.ROLE_MAY_NOT_APPROVE: "Your role does not include approving contracts.",
    DenialCode.ROLE_MAY_NOT_DRAFT: "Your role does not include drafting contracts.",
}


def _role(value: str | AppRole | None) -> AppRole:
    if isinstance(value, AppRole):
        return value
    try:
        return AppRole(str(value or "").strip().lower())
    except ValueError:
        # An unknown role is the least privileged one, not the most. Guessing "admin" from a
        # typo is how a configuration mistake becomes a security hole.
        return AppRole.SERVICE


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    code: DenialCode | None = None

    @property
    def reason(self) -> str:
        return REASONS[self.code] if self.code else ""

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "code": self.code.value if self.code else None,
            "reason": self.reason,
        }


def _allow() -> Verdict:
    return Verdict(allowed=True)


def _deny(code: DenialCode) -> Verdict:
    return Verdict(allowed=False, code=code)


def can(*, role: str | AppRole | None, permission: Permission) -> Verdict:
    """Whether the role includes this permission at all."""
    if permission in ROLE_PERMISSIONS[_role(role)]:
        return _allow()
    if permission in (Permission.APPROVE, Permission.REJECT):
        return _deny(DenialCode.ROLE_MAY_NOT_APPROVE)
    return _deny(DenialCode.ROLE_MAY_NOT_DRAFT)


def can_approve(
    *,
    actor_id: str,
    role: str | AppRole | None,
    run_owner: str = "",
    run_reviewer: str = "",
) -> Verdict:
    """Whether this person may approve *this* run.

    Checked in the order that produces the most useful reason: is this even their run, did they
    write it, are they the reviewer. The last two are not symmetric — an author is refused for a
    different reason than a stranger, and the reasons send them to different next steps.
    """
    if not actor_id:
        return _deny(DenialCode.NOT_A_PARTICIPANT)

    resolved = _role(role)
    is_owner = bool(run_owner) and actor_id == run_owner
    is_reviewer = bool(run_reviewer) and actor_id == run_reviewer

    # The author check comes first and applies to every role. An administrator who wrote a
    # contract still cannot approve it.
    if is_owner:
        return _deny(DenialCode.AUTHOR_CANNOT_APPROVE)

    if not is_reviewer and not is_owner:
        if resolved is AppRole.ADMIN:
            # An admin without the appointment is a stranger to this run, and saying so is more
            # honest than "you are not the reviewer" — nobody appointed a reviewer for them to
            # fail to be.
            return _deny(
                DenialCode.ADMIN_NOT_APPOINTED if run_reviewer else DenialCode.NOT_A_PARTICIPANT
            )
        if run_reviewer:
            return _deny(DenialCode.NOT_THE_REVIEWER)
        return _deny(DenialCode.NOT_A_PARTICIPANT)

    if resolved not in (AppRole.APPROVER, AppRole.ADMIN):
        return _deny(DenialCode.ROLE_MAY_NOT_APPROVE)

    return _allow()


def can_view(*, actor_id: str, run_owner: str = "", run_reviewer: str = "", role: str | None = None) -> Verdict:
    """R4.3: a run is visible to its participants.

    Admins are included deliberately — an operator has to be able to look at a stuck run — but
    the check is explicit rather than implied by "they are an admin so everywhere is theirs".
    """
    if not actor_id:
        return _deny(DenialCode.NOT_A_PARTICIPANT)
    if actor_id in (run_owner, run_reviewer):
        return _allow()
    if _role(role) is AppRole.ADMIN:
        return _allow()
    return _deny(DenialCode.NOT_A_PARTICIPANT)


def role_from_metadata(metadata: dict[str, str] | None) -> AppRole:
    """Read a role from account metadata, defaulting to the least privileged.

    Returns `SERVICE` for anything unrecognised: an unknown role must not fall upwards into
    approval rights.
    """
    return _role((metadata or {}).get("role", ""))
