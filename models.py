from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def normalize_name(name):
    """Ист облик за состојки/опрема: без празни места на краевите и со мали букви.
    Така „Јајца“ и „јајца “ се третираат како иста состојка."""
    return name.strip().lower()


# Врска многу-со-многу: еден рецепт користи повеќе опрема,
# а една опрема се користи во повеќе рецепти.
recipe_equipment = Table(
    "recipe_equipment",
    Base.metadata,
    Column("recipe_id", Integer, ForeignKey("recipe.id"), primary_key=True),
    Column("equipment_id", Integer, ForeignKey("equipment.id"), primary_key=True),
)


class Ingredient(Base):
    __tablename__ = "ingredient"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    # Основни состојки (сол, масло, вода...) што секој ги има дома.
    # Не се вклучуваат во скорот на совпаѓање.
    is_staple = Column(Boolean, nullable=False, default=False)

    def __repr__(self):
        return f"<Ingredient {self.name}>"


class Equipment(Base):
    __tablename__ = "equipment"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)

    def __repr__(self):
        return f"<Equipment {self.name}>"


class Recipe(Base):
    __tablename__ = "recipe"
    __table_args__ = (
        CheckConstraint("time_minutes > 0", name="ck_recipe_time_positive"),
    )

    id = Column(Integer, primary_key=True)
    title = Column(String(150), nullable=False, unique=True)
    description = Column(Text, nullable=False)
    meal_type = Column(String(20), nullable=False)  # појадок / ручек / вечера
    time_minutes = Column(Integer, nullable=False)
    steps = Column(Text, nullable=False)  # еден чекор по ред

    equipment = relationship("Equipment", secondary=recipe_equipment, backref="recipes")
    ingredient_links = relationship(
        "RecipeIngredient", back_populates="recipe", cascade="all, delete-orphan"
    )
    ai_results = relationship(
        "AIResult", back_populates="recipe", cascade="all, delete-orphan"
    )
    reviews = relationship(
        "RecipeReview", back_populates="recipe", cascade="all, delete-orphan"
    )
    saved_by = relationship(
        "SavedRecipe", back_populates="recipe", cascade="all, delete-orphan"
    )

    @property
    def steps_list(self):
        """Чекорите како листа (за {% for step in recipe.steps_list %} во Jinja)."""
        return [s for s in self.steps.split("\n") if s.strip()]

    @property
    def equipment_names(self):
        return [e.name for e in self.equipment]

    @property
    def ingredient_names(self):
        """Сите состојки на рецептот (вклучувајќи ги основните)."""
        return [link.ingredient.name for link in self.ingredient_links]

    @property
    def main_ingredient_names(self):
        """Состојки без основните (сол, масло, вода...). Се користат за скор."""
        return [
            link.ingredient.name
            for link in self.ingredient_links
            if not link.ingredient.is_staple
        ]

    @property
    def average_rating(self):
        if not self.reviews:
            return 0
        return round(sum(review.rating for review in self.reviews) / len(self.reviews), 1)

    def __repr__(self):
        return f"<Recipe {self.title}>"


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True)
    username = Column(String(30), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    reviews = relationship(
        "RecipeReview",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="RecipeReview.created_at.desc()",
    )
    saved_recipes = relationship(
        "SavedRecipe",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="SavedRecipe.created_at.desc()",
    )


class RecipeReview(Base):
    __tablename__ = "recipe_review"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating_range"),
        UniqueConstraint("user_id", "recipe_id", name="uq_review_user_recipe"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    recipe_id = Column(Integer, ForeignKey("recipe.id"), nullable=False)
    rating = Column(Integer, nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    user = relationship("User", back_populates="reviews")
    recipe = relationship("Recipe", back_populates="reviews")


class SavedRecipe(Base):
    __tablename__ = "saved_recipe"
    __table_args__ = (
        UniqueConstraint("user_id", "recipe_id", name="uq_saved_user_recipe"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    recipe_id = Column(Integer, ForeignKey("recipe.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    user = relationship("User", back_populates="saved_recipes")
    recipe = relationship("Recipe", back_populates="saved_by")


class RecipeIngredient(Base):
    """Врзна табела Recipe <-> Ingredient со количина (на пр. „200 г“)."""

    __tablename__ = "recipe_ingredient"

    recipe_id = Column(Integer, ForeignKey("recipe.id"), primary_key=True)
    ingredient_id = Column(Integer, ForeignKey("ingredient.id"), primary_key=True)
    quantity = Column(String(50), nullable=False, default="")

    recipe = relationship("Recipe", back_populates="ingredient_links")
    ingredient = relationship("Ingredient")


class Search(Base):
    """Внесот на корисникот од Create формата."""

    __tablename__ = "search"

    id = Column(Integer, primary_key=True)
    ingredients = Column(Text, nullable=False)  # одделени со запирка
    max_time = Column(Integer, nullable=False)  # 15 / 30 / 60
    equipment = Column(Text, nullable=False, default="")  # одделени со запирка
    meal_type = Column(String(20), nullable=True)  # опционално
    diet = Column(String(40), nullable=False, default="No preference")
    allergens = Column(String(200), nullable=False, default="")
    language = Column(String(2), nullable=False, default="en")
    has_basics = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    ai_results = relationship("AIResult", back_populates="search")

    @property
    def ingredients_list(self):
        return [normalize_name(i) for i in self.ingredients.split(",") if i.strip()]

    @property
    def equipment_list(self):
        return [normalize_name(e) for e in self.equipment.split(",") if e.strip()]


class AIResult(Base):
    """Зачуван AI одговор за еден рецепт и еден внес на корисникот."""

    __tablename__ = "ai_result"

    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey("recipe.id"), nullable=False)
    search_id = Column(Integer, ForeignKey("search.id"), nullable=True)
    explanation = Column(Text, nullable=False)  # зошто е добар избор
    substitutions = Column(Text, nullable=True)  # замени за состојки што недостасуваат
    tips = Column(Text, nullable=True)  # совет за вкус/служење
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    recipe = relationship("Recipe", back_populates="ai_results")
    search = relationship("Search", back_populates="ai_results")
