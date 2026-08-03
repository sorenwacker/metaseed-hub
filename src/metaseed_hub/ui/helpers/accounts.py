"""Account identity rules shared by provisioning and sharing."""


def normalize_email(email: str) -> str:
    """Reduce an email address to the form accounts are stored and matched under.

    Addresses are held lowercased so that one address means one account: the
    ``uq_users_email`` constraint compares byte-for-byte, and sharing resolves an
    invitee by equality, so both the write and the lookup must agree on casing.
    Surrounding whitespace comes from typed input and is never part of an address.

    Args:
        email: An address as typed by a sharer or reported by the identity provider.

    Returns:
        The address lowercased and stripped of surrounding whitespace.
    """
    return email.strip().lower()
