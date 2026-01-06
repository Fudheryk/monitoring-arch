from __future__ import annotations
"""server/app/infrastructure/persistence/database/models/user.py
~~~~~~~~~~~~~~~~~~~~~~~~
Table users.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, TIMESTAMP, text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base


from app.infrastructure.persistence.database.base import Base



class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False, server_default="admin_client")
    is_active = Column(Boolean(), nullable=False, server_default=text("true"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))