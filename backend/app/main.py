from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.routers.tickets import router as tickets_router
from app.routers.notifications import router as notifications_router
from app.routers.github import router as github_router
from app.routers import team_hub
from app.routers import incidents as incidents_router
from app.routers.client_requirement_change import router as client_requirement_change_router
from app.services.layer2_scorer import _load_model

# 1. Define lifespan FIRST
@asynccontextmanager
async def lifespan(app):
    _load_model()   # warm CodeBERT on startup
    yield

# 2. Create app ONCE, passing lifespan in
app = FastAPI(
    title="InternX API",
    description="AI-Powered Virtual Internship Simulator — Multiplayer Edition",
    version="2.0.0",
    docs_url="/docs" if settings.environment == "development" else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import auth, tasks, projects, mentor, chat
from app.routers.admin import router as admin_router
from app.routers.recruiter import router as recruiter_router
from app.routers.recruiter_admin_auth import router as recruiter_admin_auth_router
from app.routers.mid_sprint_change import router as mid_sprint_change_router
from app.routers.adaptive import router as adaptive_router
from app.routers.standup import router as standup_router
from app.routers.teammate_quiet import router as teammate_quiet_router

app.include_router(auth.router,            prefix="/api/auth", tags=["Auth"])
app.include_router(tasks.router)
app.include_router(projects.router)
app.include_router(mentor.router)
app.include_router(chat.router)
app.include_router(tickets_router)
app.include_router(notifications_router)
app.include_router(github_router)
app.include_router(team_hub.router)
app.include_router(admin_router)
app.include_router(recruiter_router)
app.include_router(recruiter_admin_auth_router)
app.include_router(mid_sprint_change_router)
app.include_router(adaptive_router)
app.include_router(standup_router)
app.include_router(teammate_quiet_router)
app.include_router(incidents_router.router)
app.include_router(client_requirement_change_router)

@app.get("/")
def root():
    return {"status": "ok", "app": "InternX API", "version": "2.0.0", "mode": "multiplayer"}

@app.get("/health")
def health():
    return {"status": "healthy"}