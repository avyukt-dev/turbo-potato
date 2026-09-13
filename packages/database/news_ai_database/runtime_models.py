"""Narrow durable operational controls, not a general-purpose configuration store."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .models import UUIDPrimaryKeyMixin


class RuntimeControl(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "runtime_controls"
    __table_args__ = (
        UniqueConstraint("control_key", name="uq_runtime_controls_key"),
        CheckConstraint("control_key = 'PUBLISHING_PAUSED'", name="runtime_control_key"),
        CheckConstraint("revision >= 1", name="runtime_control_revision"),
        CheckConstraint("length(reason) BETWEEN 1 AND 512", name="runtime_control_reason"),
    )
    control_key: Mapped[str] = mapped_column(String(64), nullable=False)
    boolean_value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
