import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends

from app.eventing import Event
from app.kafka_transport import broker_lifespan
from services.common import Currency, Month, UserId, internal_event_key, service_app
from .models import BudgetView, BudgetWrite, Recommendation, WeeklyReport
from .repository import BudgetRepository


def create_app(database_path: str | None = None, database_url: str | None = None):
    repository = BudgetRepository(database_path or os.getenv("BUDGET_DB_PATH", "data/budgets.db"),
                                  database_url or (os.getenv("BUDGET_DATABASE_URL") if database_path is None else None))

    @asynccontextmanager
    async def lifespan(application):
        repository.initialize()
        async with broker_lifespan(application, repository, "budgets"):
            yield

    application = service_app("Budget Service", lifespan, repository)
    application.state.repository = repository

    @application.get("/api/v1/budgets", response_model=list[BudgetView], tags=["Budgets"])
    def budgets(x_user_id: UserId, month: Month, currency: Currency = "PEN"):
        return repository.list(x_user_id, month, currency)

    @application.post("/api/v1/budgets", response_model=BudgetView, status_code=201, tags=["Budgets"])
    def create_budget(data: BudgetWrite, x_user_id: UserId):
        return repository.save(x_user_id, data)

    @application.put("/api/v1/budgets/{budget_id}", response_model=BudgetView, tags=["Budgets"])
    def update_budget(budget_id: str, data: BudgetWrite, x_user_id: UserId):
        return repository.save(x_user_id, data, budget_id)

    @application.get("/api/v1/budget-recommendations", response_model=Recommendation, tags=["Budgets"])
    def recommendations(x_user_id: UserId, month: Month, currency: Currency = "PEN"):
        return repository.recommend(x_user_id, month, currency)

    @application.get("/api/v1/reports/weekly", response_model=WeeklyReport, tags=["Reports"])
    def weekly_report(x_user_id: UserId, week_start: date, currency: Currency = "PEN"):
        return repository.weekly_report(x_user_id, week_start, currency)

    @application.post("/internal/events", dependencies=[Depends(internal_event_key)], tags=["Internal"])
    def consume(event: Event):
        return {"processed": repository.consume(event)}

    return application


app = create_app()
