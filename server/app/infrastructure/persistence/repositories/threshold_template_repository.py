# server/app/infrastructure/persistence/repositories/threshold_template_repository.py

from typing import Optional, List
from sqlalchemy.orm import Session

from app.infrastructure.persistence.database.models.threshold_template import ThresholdTemplate


class ThresholdTemplateRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_for_definition(self, definition_id) -> List[ThresholdTemplate]:
        return (
            self.db.query(ThresholdTemplate)
            .filter(ThresholdTemplate.definition_id == definition_id)
            
            .all()
        )

    def get_default_for_definition(self, definition_id) -> Optional[ThresholdTemplate]:
        return (
            self.db.query(ThresholdTemplate)
            .filter(
                ThresholdTemplate.definition_id == definition_id,
                ThresholdTemplate.name == "default",
            )
            .first()
        )

    def create(self, obj_in: dict) -> ThresholdTemplate:
        tpl = ThresholdTemplate(**obj_in)
        self.db.add(tpl)
        self.db.flush()
        return tpl
