from __future__ import annotations
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
from app.core.exceptions import NotFoundError, UnprocessableError, ConflictError as CoreConflictError
from app.repositories.exceptions import ConflictError as RepoConflictError

# Catch either flavour of ConflictError
_ConflictErrors = (CoreConflictError, RepoConflictError)

router = APIRouter()
_coaching_svc = CoachingSessionService()
_roleplay_svc = RoleplaySessionService()
_feedback_svc = FeedbackService()


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
):
    """Get a coaching session with messages and feedback. Includes intake_schema for dynamic form rendering."""
    from app.database.unit_of_work import UnitOfWork
    s = await _coaching_svc.get_session_detail(session_id)
    result = _session_dict(s)
    # Enrich with module version's intake_schema and framework info for dynamic form rendering
    try:
        async with UnitOfWork() as uow:
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
    """
    Complete a coaching session.
    Submits intake data, generates AI feedback, and marks session complete.
    """
    # Save intake
    await _coaching_svc.submit_intake(
        session_id=session_id,
        intake_data=body.intake_data,
        user_id=current_user.id,
    )

    # Generate AI feedback
    import logging as _logging
    _log = _logging.getLogger("ai_coach.sessions")
    _log.info(f"[COMPLETE] session={session_id} user={current_user.id} — starting AI feedback generation")

    try:
        from app.ai.ollama_client import OllamaClient
        from app.ai.prompt_builder import PromptBuilder
        from app.ai.coaching_engine import CoachingEngine
        from app.rag.embedding_service import EmbeddingService
        from app.rag.retrieval_service import RetrievalService
        from app.rag.citation_service import CitationService
        from app.database.unit_of_work import UnitOfWork

        async with UnitOfWork() as uow:
            session = await uow.coaching_sessions.get_by_id(session_id)
            if session is None:
                raise NotFoundError("Session not found")
            module_version_id = session.module_version_id

        _log.info(f"[COMPLETE] module_version_id={module_version_id} — calling CoachingEngine")

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

        _log.info(f"[COMPLETE] AI generation succeeded — score={ai_result.overall_score}")

        # Store feedback report
        from app.repositories.session.feedback_report_repository import FeedbackReportCreate
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
        await _feedback_svc.create_feedback_report(report_data)
        _log.info(f"[COMPLETE] Feedback report stored successfully")
        final_score = Decimal(str(ai_result.overall_score))

    except Exception as _exc:
        import traceback as _tb
        _log.error(f"[COMPLETE] AI generation FAILED — {type(_exc).__name__}: {_exc}")
        _log.error(f"[COMPLETE] Traceback:\n{_tb.format_exc()}")
        final_score = Decimal("0.00")

    # Always store a feedback report (even if AI failed — use a fallback)
    try:
        from app.repositories.session.feedback_report_repository import FeedbackReportCreate
        from app.database.unit_of_work import UnitOfWork as _UOW
        # Check if a report was already created (AI succeeded)
        existing = await _feedback_svc.get_feedback_for_session(session_id)
        if existing is None:
            _log.warning(f"[COMPLETE] No feedback report found — storing fallback report for session={session_id}")
            fallback = FeedbackReportCreate(
                user_id=current_user.id,
                overall_score=Decimal("0.00"),
                feedback_text=(
                    "We were unable to generate detailed AI feedback at this time. "
                    "Your session has been saved. Please try again or contact support."
                ),
                scores={},
                strengths=[],
                improvements=[],
                recommendations=[],
                citations=[],
                session_id=session_id,
                tenant_id=tenant_id,
                knowledge_used=False,
                model_used=None,
                raw_ai_response=None,
                next_steps="Please retry this session to get AI-powered feedback.",
            )
            await _feedback_svc.create_feedback_report(fallback)
            _log.info(f"[COMPLETE] Fallback feedback report stored")
        else:
            _log.info(f"[COMPLETE] Feedback report already exists: {existing.id}")
    except Exception as _fallback_exc:
        import traceback as _tb2
        _log.error(f"[COMPLETE] Fallback report storage FAILED: {type(_fallback_exc).__name__}: {_fallback_exc}")
        _log.error(f"[COMPLETE] Traceback:\n{_tb2.format_exc()}")

    # Mark session complete — if already completed (e.g. from a prior retry), just fetch it
    try:
        s = await _coaching_svc.complete_session(
            session_id=session_id,
            final_score=final_score,
            user_id=current_user.id,
        )
    except _ConflictErrors:
        # Session was already marked complete on a previous attempt — fetch current state
        s = await _coaching_svc.get_session_detail(session_id)
    except Exception:
        s = await _coaching_svc.get_session_detail(session_id)
    
    # Include feedback report ID in response
    result = _session_dict(s)
    try:
        report = await _feedback_svc.get_feedback_for_session(session_id)
        if report:
            result["feedback_report_id"] = str(report.id)
    except Exception:
        pass
    return result


@router.post("/coaching/{session_id}/abandon")
async def abandon_coaching_session(
    session_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """Abandon a coaching session."""
    s = await _coaching_svc.abandon_session(session_id=session_id, user_id=current_user.id)
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
):
    """Get a roleplay session."""
    s = await _roleplay_svc.get_session(session_id, user_id=current_user.id)
    return _roleplay_dict(s)


@router.post("/roleplay/{session_id}/turn")
async def submit_roleplay_turn(
    session_id: UUID,
    body: RoleplayTurnRequest,
    current_user: User = Depends(get_current_active_user),
):
    """Submit a turn in a roleplay session. Returns AI persona response."""
    from app.ai.ollama_client import OllamaClient
    from app.ai.prompt_builder import PromptBuilder
    from app.ai.roleplay_engine import RoleplayEngine
    from app.database.unit_of_work import UnitOfWork

    async with UnitOfWork() as uow:
        session = await uow.roleplay_sessions.get_by_id(session_id)
        if session is None:
            raise NotFoundError("Roleplay session not found")
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

    # Store user message
    await _roleplay_svc.add_message(
        session_id=session_id,
        role="user",
        content=body.content,
        turn_number=turn_number,
    )
    # Store persona response
    await _roleplay_svc.add_message(
        session_id=session_id,
        role="persona",
        content=result.persona_content,
        turn_number=turn_number,
        emotion_detected=result.emotion_detected,
        coaching_note=result.coaching_note,
    )
    # Update context
    if result.updated_context:
        await _roleplay_svc.update_context(session_id=session_id, context_updates=result.updated_context)

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
    """Complete a roleplay session and generate a feedback report."""
    import logging as _log
    from app.database.unit_of_work import UnitOfWork
    from app.repositories.session.feedback_report_repository import FeedbackReportCreate
    import re, json as _json
    _logger = _log.getLogger("ai_coach.sessions")

    # Step 1: Complete the session (once, at the start) — handle already-completed gracefully
    try:
        s = await _roleplay_svc.complete_session(
            session_id=session_id,
            final_score=Decimal("0.00"),
            user_id=current_user.id,
        )
    except _ConflictErrors:
        s = await _roleplay_svc.get_session(session_id, user_id=current_user.id)
    except Exception:
        s = await _roleplay_svc.get_session(session_id, user_id=current_user.id)

    # Step 2: Load messages and module version for feedback
    feedback_report_id = None
    try:
        from sqlalchemy import select as sa_select
        from app.models.session import RoleplayMessage
        from app.ai.ollama_client import OllamaClient

        async with UnitOfWork() as uow:
            session_obj = await uow.roleplay_sessions.get_by_id(session_id)
            mv = await uow.module_versions.get(session_obj.module_version_id) if session_obj else None
            msgs_result = await uow.session.execute(
                sa_select(RoleplayMessage)
                .where(RoleplayMessage.session_id == session_id)
                .order_by(RoleplayMessage.turn_number)
            )
            messages = msgs_result.scalars().all()

        rubric = (mv.scoring_rubric or {}) if mv else {}
        framework = mv.framework_name if mv else "Coaching"

        # Step 3: Generate AI feedback if messages exist, else use summary fallback
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
                ai_resp = await ollama.generate(prompt=prompt, max_tokens=500, temperature=0.3,
                    system="Reply with ONLY valid JSON.")
                content = ai_resp.content
                jm = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL) or \
                     re.search(r"\{.*\}", content, re.DOTALL)
                parsed = _json.loads(jm.group(1) if jm and "```" in content else (jm.group(0) if jm else "{}"))
            except Exception as ai_err:
                _logger.warning(f"[ROLEPLAY] AI failed: {ai_err} — using fallback")
                parsed = {}
        else:
            parsed = {}

        feedback_text = parsed.get("feedback_text") or "Your roleplay session has been completed. Keep practising to improve your skills."
        strengths = parsed.get("strengths") or []
        improvements = parsed.get("improvements") or []
        recommendations = parsed.get("recommendations") or []
        next_steps = parsed.get("next_steps") or "Continue practising with more roleplay sessions."
        model_used = None

        # Step 4: Compute rubric-driven score
        dims = rubric.get("dimensions", [])
        scores = {}
        if dims and feedback_text:
            positive = sum(1 for w in ["clear", "specific", "good", "well", "effective", "strong"] if w in feedback_text.lower())
            negative = sum(1 for w in ["vague", "unclear", "weak", "missing", "improve", "better"] if w in feedback_text.lower())
            ratio = positive / (positive + negative) if (positive + negative) > 0 else 0.6
            for dim in dims:
                name = dim.get("name", "")
                bands = dim.get("band_descriptors", {})
                max_s = len(bands) if bands else 4
                score = max(1, min(max_s, round(ratio * max_s)))
                scores[name] = {"score": score, "rationale": bands.get(str(score), f"{score}/{max_s}")}
        total_w = sum(d.get("weight", 0) for d in dims) or 1
        overall = round(sum(
            (scores.get(d["name"], {}).get("score", 2) / (len(d.get("band_descriptors", {})) or 4)) * 100 * d.get("weight", 0)
            for d in dims
        ) / total_w, 2) if dims else 0.0

        # Step 5: Store feedback report and commit
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
        saved_report = await _feedback_svc.create_feedback_report(report_data)
        feedback_report_id = str(saved_report.id)
        _logger.info(f"[ROLEPLAY] Feedback stored: id={feedback_report_id} score={overall}")

        # Step 6: Update final score on session
        async with UnitOfWork() as uow2:
            from sqlalchemy import update
            from app.models.session import RoleplaySession
            await uow2.session.execute(
                update(RoleplaySession)
                .where(RoleplaySession.id == session_id)
                .values(final_score=Decimal(str(overall)))
            )
            await uow2.commit()

    except Exception as exc:
        import traceback
        _logger.error(f"[ROLEPLAY] Feedback generation error: {type(exc).__name__}: {exc}")
        _logger.error(traceback.format_exc())
        # Store minimal fallback report so the UI always has something
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
            saved = await _feedback_svc.create_feedback_report(fallback)
            feedback_report_id = str(saved.id)
            _logger.info(f"[ROLEPLAY] Fallback report stored: {feedback_report_id}")
        except Exception:
            pass

    # Build response with feedback_report_id
    result = _roleplay_dict(s)
    if feedback_report_id:
        result["feedback_report_id"] = feedback_report_id
    return result
