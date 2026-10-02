"""Standalone, persistent open-set speaker recognition; not authentication."""

from .identity import Decision, IdentityStore, Policy, SpeakerIdentityError

__all__ = ["Decision", "IdentityStore", "Policy", "SpeakerIdentityError"]
