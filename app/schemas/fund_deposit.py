"""The fund-deposit ledger — the requiring body's money landing before
disbursement can happen. See app.models.tables.FundDeposit for why this is
its own table rather than a flag on Compensation."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class FundDepositCreate(BaseModel):
    amount: int = Field(gt=0, description="Whole rupees")
    deposited_on: date
    reference: str | None = Field(default=None, max_length=120)


class FundDepositOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    amount: int
    deposited_on: date
    reference: str | None
    recorded_by_user_id: int | None
    created_at: datetime


class FundDepositList(BaseModel):
    items: list[FundDepositOut]
    total: int
    total_deposited: int
