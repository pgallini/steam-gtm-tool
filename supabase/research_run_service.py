from __future__ import annotations

import logging
from typing import Any

from .client import SupabaseClient
from .steam_utils import canonical_steam_url, resolve_steam_appid

client = SupabaseClient()
logger = logging.getLogger(__name__)


def listResearchRuns(game_id: str) -> list[dict[str, Any]]:
    return client.select('research_runs', '*', {'game_id': f'eq.{game_id}'})


def getResearchRun(run_id: str) -> dict[str, Any] | None:
    rows = client.select('research_runs', '*', {'id': f'eq.{run_id}'})
    return rows[0] if rows else None


def createResearchRun(game_id: str, organization_id: str, name: str | None = None, run_config: dict[str, Any] | None = None, created_by: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'game_id': game_id,
        'organization_id': organization_id,
        'status': 'draft',
        'current_stage': 'intake',
        'run_config': run_config or {},
    }
    if name:
        payload['name'] = name
    if created_by:
        payload['created_by'] = created_by

    response = client.insert('research_runs', payload, returning='representation')
    return response[0] if isinstance(response, list) else response


def addCandidateControl(run_id: str, organization_id: str, control_type: str, title: str | None = None, steam_appid: int | None = None, steam_url: str | None = None, external_url: str | None = None, reason: str | None = None, user_notes: str | None = None, created_by: str | None = None, raw_input_json: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'run_id': run_id,
        'organization_id': organization_id,
        'control_type': control_type,
        'title': title,
        'steam_appid': steam_appid,
        'steam_url': steam_url,
        'external_url': external_url,
        'reason': reason,
        'user_notes': user_notes,
        'raw_input_json': raw_input_json or {},
    }
    if created_by:
        payload['created_by'] = created_by

    response = client.insert('run_candidate_controls', payload, returning='representation')
    return response[0] if isinstance(response, list) else response


def updateCandidateControl(control_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    response = client.update('run_candidate_controls', {'id': f'eq.{control_id}'}, updates, returning='representation')
    if isinstance(response, list):
        return response[0] if response else None
    return response


def deleteCandidateControl(control_id: str) -> Any:
    return client.delete('run_candidate_controls', {'id': f'eq.{control_id}'})


def listCandidateControls(run_id: str) -> list[dict[str, Any]]:
    return client.select('run_candidate_controls', '*', {'run_id': f'eq.{run_id}'})


def upsertSteamApp(appid: int, name: str | None = None, raw_appdetails_json: dict[str, Any] | None = None, raw_page_signals_json: dict[str, Any] | None = None, **extras: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {'appid': appid}
    if name:
        payload['name'] = name
    if raw_appdetails_json is not None:
        payload['raw_appdetails_json'] = raw_appdetails_json
    if raw_page_signals_json is not None:
        payload['raw_page_signals_json'] = raw_page_signals_json
    payload.update(extras)

    response = client.insert('steam_apps', payload, upsert=True, on_conflict='appid', returning='representation')
    return response[0] if isinstance(response, list) else response


def addRunEvent(run_id: str, stage: str, event_type: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any] | None:
    payload = {
        'run_id': run_id,
        'stage': stage,
        'event_type': event_type,
        'message': message,
        'details': details or {},
    }
    response = client.insert('run_events', payload, returning='representation')
    return response[0] if isinstance(response, list) else response


def recordOpenAIUsage(run_id: str, stage: str, message: str, details: dict[str, Any]) -> None:
    """Persist usage telemetry without turning a successful model response into a failed pipeline step."""
    try:
        addRunEvent(run_id, stage, 'llm_token_usage', message, details)
    except Exception:
        logger.exception('Could not persist OpenAI token usage run_id=%s stage=%s', run_id, stage)


def addRunProgressEvent(
    run_id: str,
    stage: str,
    processed_count: int,
    total_count: int | None = None,
    *,
    unit: str = 'games',
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    payload_details: dict[str, Any] = {
        'processed_count': processed_count,
        'unit': unit,
    }
    if total_count is not None:
        payload_details['total_count'] = total_count
    if details:
        payload_details.update(details)
    return addRunEvent(run_id, stage, 'stage_progress', message or f'{stage} progress', payload_details)


def updateResearchRunStatus(run_id: str, status: str, current_stage: str | None = None, failure_message: str | None = None) -> dict[str, Any] | None:
    updates: dict[str, Any] = {'status': status}
    if current_stage is not None:
        updates['current_stage'] = current_stage
    if failure_message is not None:
        updates['failure_message'] = failure_message
    response = client.update('research_runs', {'id': f'eq.{run_id}'}, updates, returning='representation')
    if isinstance(response, list):
        return response[0] if response else None
    return response


def latestRunEventAt(run_id: str, event_type: str) -> str | None:
    events = client.select('run_events', '*', {'run_id': f'eq.{run_id}', 'event_type': f'eq.{event_type}', 'order': 'created_at.desc', 'limit': '1'})
    if not events:
        return None
    return events[0].get('created_at')


def candidateSetWasApproved(run_id: str) -> bool:
    approved_at = latestRunEventAt(run_id, 'candidate_set_approved')
    if not approved_at:
        return False
    changed_at = latestRunEventAt(run_id, 'candidate_set_changed_after_approval')
    return not changed_at or changed_at <= approved_at


def markCandidateSetChangedAfterApproval(run_id: str, stage: str, message: str, details: dict[str, Any] | None = None) -> bool:
    if not candidateSetWasApproved(run_id):
        return False
    addRunEvent(run_id, stage, 'candidate_set_changed_after_approval', message, details or {})
    return True


def upsertRunCandidateFromControl(control: dict[str, Any]) -> dict[str, Any]:
    run_id = control['run_id']
    organization_id = control['organization_id']
    steam_appid = control.get('steam_appid')
    steam_url = control.get('steam_url')

    if steam_appid is not None and isinstance(steam_appid, str) and steam_appid.isdigit():
        steam_appid = int(steam_appid)

    resolved_appid, resolved_url = resolve_steam_appid(steam_appid or steam_url or control.get('external_url'))
    if resolved_appid:
        steam_appid = resolved_appid
        steam_url = steam_url or resolved_url

    if steam_appid and not steam_url:
        steam_url = canonical_steam_url(steam_appid)

    candidate_payload: dict[str, Any] = {
        'run_id': run_id,
        'organization_id': organization_id,
        'steam_appid': steam_appid,
        'title': control.get('title') or control.get('steam_url') or control.get('external_url') or 'Unnamed Candidate',
        'steam_url': steam_url,
        'external_url': control.get('external_url'),
        'primary_source': 'client_supplied' if control.get('control_type') == 'exclude' else 'user_supplied',
        'pipeline_status': 'excluded_by_user' if control.get('control_type') == 'exclude' else 'discovered',
        'user_control_type': control.get('control_type'),
        'user_control_id': control.get('id'),
        'is_user_required': control.get('control_type') in ('require_include', 'must_consider'),
        'is_user_excluded': control.get('control_type') == 'exclude',
        'is_benchmark_only': control.get('control_type') == 'benchmark_only',
        'raw_candidate_json': control,
    }

    existing = None
    if steam_appid:
        results = client.select('run_candidates', '*', {'run_id': f'eq.{run_id}', 'steam_appid': f'eq.{steam_appid}'})
        existing = results[0] if results else None

    if existing:
        merged_updates = {
            'steam_url': steam_url or existing.get('steam_url'),
            'external_url': control.get('external_url') or existing.get('external_url'),
            'primary_source': candidate_payload['primary_source'] or existing.get('primary_source'),
            'pipeline_status': 'excluded_by_user' if candidate_payload['is_user_excluded'] else existing.get('pipeline_status') or candidate_payload['pipeline_status'],
            'user_control_type': control.get('control_type') or existing.get('user_control_type'),
            'user_control_id': control.get('id') or existing.get('user_control_id'),
            'is_user_required': existing.get('is_user_required') or candidate_payload['is_user_required'],
            'is_user_excluded': existing.get('is_user_excluded') or candidate_payload['is_user_excluded'],
            'is_benchmark_only': existing.get('is_benchmark_only') or candidate_payload['is_benchmark_only'],
            'raw_candidate_json': control,
        }
        response = client.update('run_candidates', {'id': f"eq.{existing['id']}"}, merged_updates, returning='representation')
        candidate = response[0] if isinstance(response, list) else response
    else:
        response = client.insert('run_candidates', candidate_payload, returning='representation')
        candidate = response[0] if isinstance(response, list) else response

    if candidate is None:
        raise RuntimeError('Unable to upsert run candidate from control')

    evidence_payload = {
        'candidate_id': candidate['id'],
        'run_id': run_id,
        'source': candidate_payload['primary_source'],
        'query': control.get('title') or control.get('steam_url') or control.get('external_url'),
        'source_rank': None,
        'source_score': None,
        'evidence_notes': control.get('reason') or control.get('user_notes'),
        'raw_evidence_json': control,
    }
    client.insert('candidate_discovery_evidence', evidence_payload, returning='representation')

    return candidate


def prepareRunCandidates(run_id: str) -> dict[str, Any]:
    run = getResearchRun(run_id)
    if run is None:
        raise ValueError(f'Research run {run_id} not found')

    approved_before_run = candidateSetWasApproved(run_id)
    updateResearchRunStatus(run_id, 'running', current_stage='discovery')
    addRunEvent(run_id, 'discovery', 'script_started', 'prepare_run_candidates started')

    controls = listCandidateControls(run_id)
    if not controls:
        addRunEvent(run_id, 'discovery', 'no_controls_found', 'No candidate controls were found for this run.')
        addRunEvent(run_id, 'discovery', 'stage_completed', 'Known games and guidance applied', {'processed_count': 0, 'unit': 'games'})
        updateResearchRunStatus(run_id, 'completed', current_stage='discovery')
        return {'status': 'no_controls_found', 'count': 0}

    candidate_count = 0
    total_controls = len(controls)
    for index, control in enumerate(controls, start=1):
        steam_appid = control.get('steam_appid')
        steam_url = control.get('steam_url')

        if steam_appid is None and steam_url:
            resolved_appid, resolved_url = resolve_steam_appid(steam_url)
            if resolved_appid:
                steam_appid = resolved_appid
                steam_url = resolved_url
                control['steam_appid'] = steam_appid
                control['steam_url'] = steam_url

        if steam_appid and not control.get('steam_url'):
            control['steam_url'] = canonical_steam_url(steam_appid)

        if steam_appid:
            if isinstance(steam_appid, str) and steam_appid.isdigit():
                steam_appid = int(steam_appid)
            upsertSteamApp(steam_appid, name=control.get('title'))

        upsertRunCandidateFromControl(control)
        candidate_count += 1
        addRunProgressEvent(
            run_id,
            'discovery',
            candidate_count,
            total_controls,
            unit='games',
            message='Applying known games and guidance',
            details={'control_id': control.get('id'), 'title': control.get('title')},
        )

    addRunEvent(run_id, 'discovery', 'script_completed', 'prepare_run_candidates completed successfully')
    addRunEvent(run_id, 'discovery', 'stage_completed', 'Known games and guidance applied', {'processed_count': candidate_count, 'total_count': total_controls, 'unit': 'games'})
    if approved_before_run and candidate_count > 0:
        markCandidateSetChangedAfterApproval(
            run_id,
            'discovery',
            'Known games and guidance were updated after candidate approval',
            {'processed_count': candidate_count, 'total_count': total_controls, 'unit': 'games'},
        )
    updateResearchRunStatus(run_id, 'completed', current_stage='discovery')
    return {'status': 'completed', 'count': candidate_count}
