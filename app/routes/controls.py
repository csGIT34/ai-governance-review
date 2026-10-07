from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.models import User
from app.routes import ai
from app.routes.common import load_assessment

router = APIRouter()


@router.get("/assessments/{aid:int}")
def assessment_page(aid: int, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_session)):
    a = load_assessment(db, aid)
    if a.kind == "checklist":
        return ai.review_page(request, db, user, a)
    raise NotImplementedError
