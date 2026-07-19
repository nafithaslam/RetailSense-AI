"""
RetailSense AI — repositories package

Repository classes provide async data-access abstractions over SQLAlchemy
ORM models.  They contain **no business logic** — only queries, inserts,
updates, and deletes.  All coordination of multiple repository calls or
cross-cutting concerns (validation, token generation, email sending, …)
belongs in the service layer.

Pattern
-------
    # app/repositories/product_repository.py
    class ProductRepository:
        def __init__(self, db: AsyncSession) -> None:
            self.db = db

        async def get_by_id(self, product_id: int) -> Product | None:
            result = await self.db.execute(
                select(Product).where(Product.id == product_id)
            )
            return result.scalar_one_or_none()
"""

from app.repositories.user_repository import UserRepository  # noqa: F401

__all__ = ["UserRepository"]
