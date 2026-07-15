# FILE: backend/app/api/v1/routers/sessions.py
from __future__ import annotations

import logging
import re
import json as _json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status, BackgroundTasks
from pydantic import BaseModel

from app.schemas.common import MessageResponse
from app.schemas.session.coaching_session import (
    CoachingSessionCreate, SessionCompleteRequest,
)
from app.schemas.session.roleplay_session import (
    RoleplaySessionCreate, RoleplayTurnRequest,
)
from app.services.session.coaching_session_service import CoachingSessionService
from app.services.session.roleplay_session_service import RoleplaySessionService
from app.services.session.feedback_service import FeedbackService
from app.api.v1.dependencies.auth import get_current_active_user, get_current_tenant_id
from app.models.user import User
from app.core.exceptions import (
    NotFoundError,
    UnprocessableError,
    PermissionDeniedError,
    ContentSafetyError,
    CrisisContentDetectedError,
    ConflictError as CoreConflictError,
)
from app.repositories.exceptions import ConflictError as RepoConflictError
from app.database.unit_of_work import UnitOfWork
from app.repositories.session.feedback_report_repository import FeedbackReportCreate
from app.repositories.analytics.analytics_repository import AuditLogCreate
from app.ai.safety_engine import SafetyEngine

# Catch either flavour of ConflictError
_ConflictErrors = (CoreConflictError, RepoConflictError)

router = APIRouter()
_coaching_svc = CoachingSessionService()
_roleplay_svc = RoleplaySessionService()
_feedback_svc = FeedbackService()
_logger = logging.getLogger("ai_coach.sessions")


def _session_dict(s) -> dict:
    return {
        "id": str(s.id),
        "user_id": str(s.user_id),
        "module_id": str(s.module_id),
        "module_version_id": str(s.module_version_id),
        "status": s.status,
        "intake_data": s.intake_data,
        "final_score": float(s.final_score) if s.final_score else None,
        "duration_seconds": s.duration_seconds,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
        "version": s.version,
    }


def _roleplay_dict(s) -> dict:
    return {
        "id": str(s.id),
        "user_id": str(s.user_id),
        "module_id": str(s.module_id),
        "status": s.status,
        "turn_count": s.turn_count,
        "scenario_prompt": s.scenario_prompt,
        "final_score": float(s.final_score) if s.final_score else None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        "created_at": s.created_at.isoformat(),
        "version": s.version,
    }


async def _log_safety_block(
    *,
    check,
    tenant_id: UUID | None,
    user_id: UUID,
    entity_type: str,
    entity_id: UUID | None,
) -> None:
    """
    Persist every safety block to the audit trail — crisis, keyword,
    injection, and length violations alike. Best-effort: a logging
    failure must never block the user-facing response.
    """
    try:
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            await uow.analytics.write_audit_log(
                AuditLogCreate(
                    action="CONTENT_SAFETY_BLOCK",
                    entity_type=entity_type,
                    entity_id=entity_id,
                    actor_user_id=user_id,
                    tenant_id=tenant_id,
                    after_state={
                        "category": check.category,
                        "reason": check.reason,
                    },
                )
            )
            await uow.commit()
    except Exception as audit_exc:
        _logger.error(f"[SAFETY] Failed to write audit log: {audit_exc}")


# ── Coaching sessions ──────────────────────────────────────────────────────────

@router.post("/coaching", status_code=status.HTTP_201_CREATED)
async def create_coaching_session(
    body: CoachingSessionCreate,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Start a new coaching session."""
    s = await _coaching_svc.create_session(
        user_id=current_user.id,
        module_id=body.module_id,
        tenant_id=tenant_id,
    )
    return _session_dict(s)


@router.get("/coaching")
async def list_coaching_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session_status: str | None = Query(None, alias="status"),
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """List coaching sessions for the current user."""
    result = await _coaching_svc.list_sessions(
        user_id=current_user.id, tenant_id=tenant_id, status=session_status, page=page, page_size=page_size
    )
    return {"items": [_session_dict(s) for s in result.items], "total": result.total}


@router.get("/coaching/{session_id}")
async def get_coaching_session(
    session_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Securely return a coaching session details."""
    s = await _coaching_svc.get_session_detail(
        session_id=session_id,
        user_id=current_user.id,
        tenant_id=tenant_id,
    )
    result = _session_dict(s)
    try:
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            mv = await uow.module_versions.get(s.module_version_id)
            if mv:
                result["intake_schema"] = mv.intake_schema or []
                result["framework_name"] = mv.framework_name or ""
                result["scoring_rubric"] = mv.scoring_rubric or {}
    except Exception:
        result["intake_schema"] = []
        result["framework_name"] = ""
        result["scoring_rubric"] = {}
    return result


@router.post("/coaching/{session_id}/complete")
async def complete_coaching_session(
    session_id: UUID,
    body: SessionCompleteRequest,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Complete a coaching session securely."""
    # Safety check on learner intake BEFORE it's persisted or sent to the
    # LLM — enforced by the engine, not left to prompt wording (PRD A.4/B.5).
    _safety = SafetyEngine()
    for _field_name, _field_value in body.intake_data.items():
        if not isinstance(_field_value, str):
            continue
        _check = await _safety.check_content(_field_value)
        if not _check.is_safe:
            _logger.warning(
                f"[SAFETY] Blocked coaching intake — session={session_id} "
                f"user={current_user.id} field={_field_name} category={_check.category}"
            )
            await _log_safety_block(
                check=_check,
                tenant_id=tenant_id,
                user_id=current_user.id,
                entity_type="coaching_session",
                entity_id=session_id,
            )
            if _check.category == "crisis":
                raise CrisisContentDetectedError()
            raise ContentSafetyError(
                f"Your submission for '{_field_name}' could not be processed: {_check.reason}"
            )

    await _coaching_svc.submit_intake(
        session_id=session_id,
        intake_data=body.intake_data,
        user_id=current_user.id,
        tenant_id=tenant_id,
    )

    _logger.info(f"[COMPLETE] session={session_id} user={current_user.id} — starting AI feedback generation")

    try:
        from app.ai.ollama_client import OllamaClient
        from app.ai.prompt_builder import PromptBuilder
        from app.ai.coaching_engine import CoachingEngine
        from app.rag.embedding_service import EmbeddingService
        from app.rag.retrieval_service import RetrievalService
        from app.rag.citation_service import CitationService

        async with UnitOfWork(tenant_id=tenant_id) as uow:
            session = await uow.coaching_sessions.get_by_id(session_id, tenant_id=tenant_id)
            if session is None:
                raise NotFoundError("Session not found")
            module_version_id = session.module_version_id

        ollama = OllamaClient()
        builder = PromptBuilder()
        embedding_svc = EmbeddingService()
        retrieval_svc = RetrievalService(embedding_service=embedding_svc)
        citation_svc = CitationService()
        engine = CoachingEngine(
            ollama_client=ollama,
            prompt_builder=builder,
            retrieval_service=retrieval_svc,
            citation_service=citation_svc,
        )

        ai_result = await engine.generate_feedback(
            session_id=session_id,
            user_id=current_user.id,
            module_version_id=module_version_id,
            tenant_id=tenant_id,
            intake_data=body.intake_data,
        )

        report_data = FeedbackReportCreate(
            user_id=current_user.id,
            overall_score=Decimal(str(ai_result.overall_score)),
            feedback_text=ai_result.feedback_text,
            scores=ai_result.scores,
            strengths=ai_result.strengths,
            improvements=ai_result.improvements,
            recommendations=ai_result.recommendations,
            citations=ai_result.citations,
            session_id=session_id,
            tenant_id=tenant_id,
            knowledge_used=ai_result.knowledge_used,
            model_used=ai_result.generation_metadata.get("model_used"),
            raw_ai_response=ai_result.raw_ai_response,
            next_steps=ai_result.next_steps,
        )
        await _feedback_svc.create_feedback_report(report_data, tenant_id=tenant_id)
        final_score = Decimal(str(ai_result.overall_score))

    except Exception as _exc:
        _logger.error(f"[COMPLETE] AI generation FAILED — {type(_exc).__name__}: {_exc}")
        final_score = Decimal("0.00")

    try:
        existing = await _feedback_svc.get_feedback_for_session(session_id, tenant_id=tenant_id)
        if existing is None:
            fallback = FeedbackReportCreate(
                user_id=current_user.id,
                overall_score=Decimal("0.00"),
                feedback_text="We were unable to generate detailed AI feedback at this time.",
                scores={}, strengths=[], improvements=[], recommendations=[], citations=[],
                session_id=session_id, tenant_id=tenant_id, knowledge_used=False,
                model_used=None, raw_ai_response=None,
                next_steps="Please retry this session to get AI-powered feedback.",
            )
            await _feedback_svc.create_feedback_report(fallback, tenant_id=tenant_id)
    except Exception:
        pass

    try:
        s = await _coaching_svc.complete_session(
            session_id=session_id,
            final_score=final_score,
            user_id=current_user.id,
            tenant_id=tenant_id,
        )
    except _ConflictErrors:
        s = await _coaching_svc.get_session_detail(session_id, user_id=current_user.id, tenant_id=tenant_id)
    except Exception:
        s = await _coaching_svc.get_session_detail(session_id, user_id=current_user.id, tenant_id=tenant_id)

    result = _session_dict(s)
    try:
        report = await _feedback_svc.get_feedback_for_session(session_id, tenant_id=tenant_id)
        if report:
            result["feedback_report_id"] = str(report.id)
    except Exception:
        pass
    return result


@router.post("/coaching/{session_id}/abandon")
async def abandon_coaching_session(
    session_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Abandon a coaching session."""
    s = await _coaching_svc.abandon_session(session_id=session_id, user_id=current_user.id, tenant_id=tenant_id)
    return _session_dict(s)


# ── Roleplay sessions ──────────────────────────────────────────────────────────

@router.post("/roleplay", status_code=status.HTTP_201_CREATED)
async def create_roleplay_session(
    body: RoleplaySessionCreate,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Start a new roleplay session."""
    s = await _roleplay_svc.create_session(
        user_id=current_user.id,
        module_id=body.module_id,
        tenant_id=tenant_id,
        persona_id=body.persona_id,
        scenario_prompt=body.scenario_prompt,
    )
    return _roleplay_dict(s)


@router.get("/roleplay")
async def list_roleplay_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """List roleplay sessions for the current user."""
    result = await _roleplay_svc.list_sessions(
        user_id=current_user.id, tenant_id=tenant_id, page=page, page_size=page_size
    )
    return {"items": [_roleplay_dict(s) for s in result.items], "total": result.total}


@router.get("/roleplay/{session_id}")
async def get_roleplay_session(
    session_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Get a roleplay session."""
    s = await _roleplay_svc.get_session(
        session_id, user_id=current_user.id, tenant_id=tenant_id
    )
    return _roleplay_dict(s)


@router.post("/roleplay/{session_id}/turn")
async def submit_roleplay_turn(
    session_id: UUID,
    body: RoleplayTurnRequest,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Submit a turn in a roleplay session. Returns AI persona response."""
    from app.ai.ollama_client import OllamaClient
    from app.ai.prompt_builder import PromptBuilder
    from app.ai.roleplay_engine import RoleplayEngine

    # Safety check on the learner's message BEFORE it reaches the LLM.
    _safety = SafetyEngine()
    _check = await _safety.check_content(body.content)
    if not _check.is_safe:
        _logger.warning(
            f"[SAFETY] Blocked roleplay turn — session={session_id} "
            f"user={current_user.id} category={_check.category}"
        )
        await _log_safety_block(
            check=_check,
            tenant_id=tenant_id,
            user_id=current_user.id,
            entity_type="roleplay_session",
            entity_id=session_id,
        )
        if _check.category == "crisis":
            raise CrisisContentDetectedError()
        raise ContentSafetyError(f"Your message could not be processed: {_check.reason}")

    async with UnitOfWork(tenant_id=tenant_id) as uow:
        session = await uow.roleplay_sessions.get_by_id(session_id, tenant_id=tenant_id)
        if session is None:
            raise NotFoundError("Roleplay session not found")
        if session.user_id != current_user.id:
            raise PermissionDeniedError(
                "You do not have permission to access this session."
            )
        turn_number = session.turn_count + 1
        module_version_id = session.module_version_id
        persona_id = session.persona_id
        context = session.context or {}

    ollama = OllamaClient()
    builder = PromptBuilder()
    engine = RoleplayEngine(ollama_client=ollama, prompt_builder=builder)

    try:
        result = await engine.generate_turn(
            session_id=session_id,
            user_message=body.content,
            persona_id=persona_id,
            module_version_id=module_version_id,
            turn_number=turn_number,
            conversation_history=[],
            session_context=context,
        )
    except Exception as exc:
        raise UnprocessableError(f"AI generation failed: {exc}") from exc

    # Safety check on the model's OWN output too — enforced by the engine
    # on both sides of the conversation, not just learner input.
    _output_check = await _safety.check_content(result.persona_content)
    if not _output_check.is_safe:
        _logger.error(
            f"[SAFETY] Blocked persona OUTPUT — session={session_id} "
            f"category={_output_check.category}"
        )
        await _log_safety_block(
            check=_output_check,
            tenant_id=tenant_id,
            user_id=current_user.id,
            entity_type="roleplay_session",
            entity_id=session_id,
        )
        if _output_check.category == "crisis":
            # The MODEL produced crisis-adjacent language — still route
            # to the supportive response rather than a generic failure.
            raise CrisisContentDetectedError()
        raise ContentSafetyError(
            "The AI response could not be delivered due to a safety check. "
            "Please try rephrasing your message."
        )

    # Store user message
    await _roleplay_svc.add_message(
        session_id=session_id,
        role="user",
        content=body.content,
        turn_number=turn_number,
        tenant_id=tenant_id,
    )
    # Store persona response
    await _roleplay_svc.add_message(
        session_id=session_id,
        role="persona",
        content=result.persona_content,
        turn_number=turn_number,
        emotion_detected=result.emotion_detected,
        coaching_note=result.coaching_note,
        tenant_id=tenant_id,
    )
    # Update context
    if result.updated_context:
        await _roleplay_svc.update_context(
            session_id=session_id,
            context_updates=result.updated_context,
            tenant_id=tenant_id,
        )

    return {
        "session_id": str(session_id),
        "turn_number": turn_number,
        "persona_content": result.persona_content,
        "emotion_detected": result.emotion_detected,
        "session_status": "active",
        "turn_count": turn_number,
    }


@router.post("/roleplay/{session_id}/complete")
async def complete_roleplay_session(
    session_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Complete a roleplay session and generate a feedback report securely."""
    from app.models.session import RoleplayMessage
    from app.ai.ollama_client import OllamaClient
    from sqlalchemy import select as sa_select

    try:
        s = await _roleplay_svc.complete_session(
            session_id=session_id,
            final_score=Decimal("0.00"),
            user_id=current_user.id,
            tenant_id=tenant_id,
        )
    except _ConflictErrors:
        s = await _roleplay_svc.get_session(session_id, user_id=current_user.id, tenant_id=tenant_id)
    except Exception:
        s = await _roleplay_svc.get_session(session_id, user_id=current_user.id, tenant_id=tenant_id)

    feedback_report_id = None
    try:
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            session_obj = await uow.roleplay_sessions.get_by_id(session_id, tenant_id=tenant_id)
            mv = await uow.module_versions.get(session_obj.module_version_id) if session_obj else None
            msgs_result = await uow.session.execute(
                sa_select(RoleplayMessage)
                .where(RoleplayMessage.session_id == session_id)
                .order_by(RoleplayMessage.turn_number)
            )
            messages = msgs_result.scalars().all()

        rubric = (mv.scoring_rubric or {}) if mv else {}
        framework = mv.framework_name if mv else "Coaching"

        if messages:
            convo = "\n".join(
                f"{'Learner' if m.role == 'user' else 'Persona'}: {m.content}"
                for m in messages
            )
            prompt = f"""You are an expert coach reviewing a roleplay conversation.
Framework: {framework}

Conversation:
{convo[:2000]}

Respond with ONLY this JSON:
{{"feedback_text":"2-3 sentences coaching feedback on how the learner performed","strengths":["strength from conversation"],"improvements":["area to improve"],"recommendations":[{{"priority":1,"area":"Communication","suggestion":"specific tip"}}],"next_steps":"one concrete action"}}"""
            try:
                ollama = OllamaClient()
                ai_resp = await ollama.generate(prompt=prompt, max_tokens=500, temperature=0.3, system="Reply with ONLY valid JSON.")
                content = ai_resp.content
                jm = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL) or re.search(r"\{.*\}", content, re.DOTALL)
                parsed = _json.loads(jm.group(1) if jm and "```" in content else (jm.group(0) if jm else "{}"))
            except Exception as ai_err:
                _logger.warning(f"[ROLEPLAY] AI failed: {ai_err}")
                parsed = {}
        else:
            parsed = {}

        feedback_text = parsed.get("feedback_text") or "Your roleplay session has been completed."
        strengths = parsed.get("strengths") or []
        improvements = parsed.get("improvements") or []
        recommendations = parsed.get("recommendations") or []
        next_steps = parsed.get("next_steps") or "Continue practising."
        model_used = None

        # Safety check on the model's own output — same enforcement as the
        # coaching path (CoachingEngine.generate_feedback). A raise here is
        # caught by this function's own outer except block below, which
        # already falls back to a safe generic message and still completes
        # the session — consistent with how AI-generation failures are
        # handled everywhere else in this endpoint.
        _rp_safety_check = await SafetyEngine().check_content(feedback_text)
        if not _rp_safety_check.is_safe:
            _logger.error(
                f"[ROLEPLAY] SAFETY BLOCK on feedback output — session={session_id} "
                f"category={_rp_safety_check.category}"
            )
            await _log_safety_block(
                check=_rp_safety_check,
                tenant_id=tenant_id,
                user_id=current_user.id,
                entity_type="roleplay_session",
                entity_id=session_id,
            )
            # Caught by this function's own outer except block below,
            # which already falls back to a safe generic message —
            # crisis and non-crisis both land there the same way here,
            # since this is post-hoc feedback generation, not a live
            # conversational turn the learner is waiting on.
            raise ContentSafetyError(
                "Generated roleplay feedback did not pass a safety check."
            )

        # Real, model-generated rubric scoring — NOT keyword counting.
        # This mirrors the P0-3 fix already applied in CoachingEngine;
        # this roleplay path had its own separate keyword heuristic that
        # was missed in that earlier pass.
        scores = {}
        overall = 0.0
        if rubric.get("dimensions") and feedback_text:
            from app.ai.prompt_builder import PromptBuilder as _PromptBuilder
            from app.ai.scoring_engine import ScoringEngine as _ScoringEngine

            _scoring_ollama = OllamaClient()
            _scoring_builder = _PromptBuilder()
            _scoring_engine = _ScoringEngine(
                ollama_client=_scoring_ollama, prompt_builder=_scoring_builder
            )
            try:
                score_response = await _scoring_engine.score_session(
                    session_id=session_id,
                    feedback_text=feedback_text,
                    rubric=rubric,
                    intake_data={"conversation": convo[:2000]} if messages else {},
                )
                scores = {
                    dim.dimension_name: {
                        "score": dim.score,
                        "rationale": dim.rationale,
                    }
                    for dim in score_response.score_breakdown.dimensions
                }
                overall = score_response.score_breakdown.overall_score
            except Exception as score_err:
                _logger.warning(f"[ROLEPLAY] Scoring failed: {score_err}")
                scores = {}
                overall = 0.0

        report_data = FeedbackReportCreate(
            user_id=current_user.id,
            overall_score=Decimal(str(overall)),
            feedback_text=feedback_text,
            scores=scores,
            strengths=strengths,
            improvements=improvements,
            recommendations=recommendations,
            citations=[],
            roleplay_id=session_id,
            tenant_id=tenant_id,
            knowledge_used=False,
            model_used=model_used,
            raw_ai_response=None,
            next_steps=next_steps,
        )
        saved_report = await _feedback_svc.create_feedback_report(report_data, tenant_id=tenant_id)
        feedback_report_id = str(saved_report.id)

        async with UnitOfWork(tenant_id=tenant_id) as uow2:
            from sqlalchemy import update
            from app.models.session import RoleplaySession
            await uow2.session.execute(
                update(RoleplaySession)
                .where(RoleplaySession.id == session_id)
                .values(final_score=Decimal(str(overall)))
            )
            await uow2.commit()

    except Exception as exc:
        try:
            fallback = FeedbackReportCreate(
                user_id=current_user.id,
                overall_score=Decimal("0.00"),
                feedback_text="Your roleplay session was completed. AI feedback could not be generated at this time.",
                scores={}, strengths=[], improvements=[], recommendations=[], citations=[],
                roleplay_id=session_id, tenant_id=tenant_id, knowledge_used=False,
                model_used=None, raw_ai_response=None,
                next_steps="Try completing another roleplay session.",
            )
            saved = await _feedback_svc.create_feedback_report(fallback, tenant_id=tenant_id)
            feedback_report_id = str(saved.id)
        except Exception:
            pass

    result = _roleplay_dict(s)
    if feedback_report_id:
        result["feedback_report_id"] = feedback_report_id
    return result