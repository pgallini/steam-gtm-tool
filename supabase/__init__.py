"""Supabase helper package for Steam GTM Intelligence Assistant."""

from .client import SupabaseClient
from .research_run_service import (
    addCandidateControl,
    addRunEvent,
    createResearchRun,
    deleteCandidateControl,
    getResearchRun,
    listCandidateControls,
    listResearchRuns,
    updateCandidateControl,
    updateResearchRunStatus,
    upsertRunCandidateFromControl,
    upsertSteamApp,
)
from .steam_utils import resolve_steam_appid, canonical_steam_url
