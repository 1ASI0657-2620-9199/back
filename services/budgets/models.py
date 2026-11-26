from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from services.common import Currency, Money, Month


class BudgetWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    category: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    month: Month
    currency: Currency = "PEN"
    limit_amount: Money
    alert_threshold: int = Field(default=80, ge=1, le=100)


class BudgetView(BudgetWrite):
    model_config = ConfigDict(extra="ignore")
    id: str
    spent: Decimal
    remaining: Decimal
    percentage: Decimal
    status: Literal["on_track", "near_limit", "exceeded"]
    revision: int
    created_at: datetime


class RecommendedBudget(BaseModel):
    category: str
    limit_amount: Decimal
    share: Decimal


class Recommendation(BaseModel):
    month: str
    currency: str
    average_monthly_income: Decimal
    savings_target: Decimal
    historical_months: list[str]
    explanation: str
    budgets: list[RecommendedBudget]


class ReportCategory(BaseModel):
    category: str
    expenses: Decimal


class WeeklyReport(BaseModel):
    week_start: date
    week_end: date
    currency: str
    income: Decimal
    expenses: Decimal
    balance: Decimal
    transaction_count: int
    categories: list[ReportCategory]
    budgets: list[BudgetView]
    suggestions: list[str]
    sufficient_data: bool
